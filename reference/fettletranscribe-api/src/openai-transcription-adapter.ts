import { AIProviderError } from "./errors.js";
import { logInfo, logWarn } from "./logger.js";
import {
  buildOpenAITranscriptionForm,
  delay,
  normalizeAudioForOpenAI,
  splitAudioForTranscription,
  type StoredSessionAudio
} from "./transcription-audio-utils.js";
import {
  type AdapterResponse,
  adapterResponseSchema,
  buildAdapterResult,
  classifyProviderRetryReason,
  computeRetryDelayMs,
  extractOpenAIResponseText,
  extractOpenAITranscriptSegments,
  getTodayDateInputValue,
  isOpenAIAudioFormatError,
  isRecord,
  isSynthesisJsonRecoverableError,
  normalizeAdapterResponse,
  normalizeOpenAITranscriptForSynthesis,
  OPENAI_TRANSCRIPTION_MAX_ATTEMPTS,
  OPENAI_TRANSCRIPTION_RETRY_DELAY_MS,
  openAITranscriptSynthesisPrompt,
  parseJsonObject,
  readProviderErrorBody,
  runChunkedTranscription,
  SHARED_CHUNK_TRANSCRIBE_MAX_BYTES,
  shouldPreNormalizeOpenAIAudio,
  shouldRetryProviderStatus,
  SYNTHESIS_JSON_REPAIR_MAX_ATTEMPTS,
  type OpenAIAdapterOptions,
  type TranscriptionAdapter,
  type TranscriptionAdapterInput,
  type TranscriptionAdapterResult
} from "./transcription-adapter-shared.js";

export class OpenAITranscriptionAdapter implements TranscriptionAdapter {
  private readonly apiKey: string;
  private readonly transcribeModel: string;
  private readonly synthesisModel: string;
  private readonly baseUrl: string;
  private readonly fetchImpl: typeof fetch;
  private readonly splitAudioImpl: typeof splitAudioForTranscription;
  private readonly normalizeAudioImpl: typeof normalizeAudioForOpenAI;
  readonly providerName: string;

  constructor(options: OpenAIAdapterOptions) {
    this.apiKey = options.apiKey;
    this.transcribeModel =
      options.transcribeModel ?? "gpt-4o-transcribe-diarize";
    this.synthesisModel = options.synthesisModel ?? "gpt-5.4-mini";
    this.baseUrl = options.baseUrl ?? "https://api.openai.com/v1";
    this.fetchImpl = options.fetchImpl ?? fetch;
    this.splitAudioImpl = options.splitAudioImpl ?? splitAudioForTranscription;
    this.normalizeAudioImpl = options.normalizeAudioImpl ?? normalizeAudioForOpenAI;
    this.providerName = `openai:${this.transcribeModel}+${this.synthesisModel}`;
  }

  async transcribe(
    input: TranscriptionAdapterInput
  ): Promise<TranscriptionAdapterResult> {
    if (input.audio.data.length > SHARED_CHUNK_TRANSCRIBE_MAX_BYTES) {
      return this.transcribeChunked(input);
    }

    const transcriptSegments = await this.requestAudioTranscription(
      input,
      input.audio,
      0
    );
    const cleanedSegments = normalizeOpenAITranscriptForSynthesis(
      transcriptSegments,
      input
    );
    const synthesis = await this.synthesizeTranscript(input, cleanedSegments);

    return buildAdapterResult(this.providerName, {
      ...synthesis,
      transcriptSegments: cleanedSegments
    }, input);
  }

  private async transcribeChunked(
    input: TranscriptionAdapterInput
  ): Promise<TranscriptionAdapterResult> {
    const { mergedSegments } = await runChunkedTranscription(input, {
      providerName: this.providerName,
      splitAudioImpl: this.splitAudioImpl,
      requestChunkTranscription: async (chunk, _chunkIndex, _chunkCount, offsetSeconds) =>
        ({
          segments: await this.requestAudioTranscription(input, chunk, offsetSeconds),
          alreadyOffsetApplied: true
        })
    });

    const cleanedSegments = normalizeOpenAITranscriptForSynthesis(
      mergedSegments,
      input
    );
    const synthesis = await this.synthesizeTranscript(input, cleanedSegments);

    return buildAdapterResult(this.providerName, {
      ...synthesis,
      transcriptSegments: cleanedSegments
    }, input);
  }

  private async requestAudioTranscription(
    input: TranscriptionAdapterInput,
    audio: StoredSessionAudio,
    offsetSeconds: number
  ): Promise<AdapterResponse["transcriptSegments"]> {
    const transcriptionUrl = `${this.baseUrl}/audio/transcriptions`;
    let lastStatus = 0;
    let useNormalizedAudio = shouldPreNormalizeOpenAIAudio(audio);
    let normalizedAudio: StoredSessionAudio | null = null;

    if (useNormalizedAudio) {
      const preNormalized = await this.normalizeAudioImpl(audio, input.signal);
      normalizedAudio = preNormalized;
      useNormalizedAudio =
        preNormalized.fileName !== audio.fileName ||
        preNormalized.mimeType !== audio.mimeType ||
        preNormalized.data.length !== audio.data.length;
      logInfo(
        "openai_audio_pre_normalization_evaluated",
        {
          sessionId: input.sessionId,
          sourceMimeType: audio.mimeType,
          sourceSizeBytes: audio.data.length,
          normalizedMimeType: preNormalized.mimeType,
          normalizedSizeBytes: preNormalized.data.length,
          normalizationApplied: useNormalizedAudio
        },
        { scope: "transcription" }
      );
    }

    for (let attempt = 1; attempt <= OPENAI_TRANSCRIPTION_MAX_ATTEMPTS; attempt += 1) {
      const attemptAudio = useNormalizedAudio
        ? (normalizedAudio ??= await this.normalizeAudioImpl(audio, input.signal))
        : audio;
      const response = await this.fetchImpl(transcriptionUrl, {
        method: "POST",
        headers: {
          Authorization: `Bearer ${this.apiKey}`
        },
        ...(input.signal ? { signal: input.signal } : {}),
        body: buildOpenAITranscriptionForm(attemptAudio, this.transcribeModel)
      });

      if (response.ok) {
        const payload: unknown = await response.json();
        return extractOpenAITranscriptSegments(payload, input, offsetSeconds);
      }

      lastStatus = response.status;
      const responseBody = await readProviderErrorBody(response);
      const retryReason = classifyProviderRetryReason(response.status);
      const willRetryByStatus =
        attempt < OPENAI_TRANSCRIPTION_MAX_ATTEMPTS &&
        shouldRetryProviderStatus(response.status);
      const retryDelayMs = willRetryByStatus
        ? computeRetryDelayMs(OPENAI_TRANSCRIPTION_RETRY_DELAY_MS, attempt)
        : 0;
      logWarn(
        "openai_transcription_request_failed",
        {
          sessionId: input.sessionId,
          attempt,
          maxAttempts: OPENAI_TRANSCRIPTION_MAX_ATTEMPTS,
          willRetry: willRetryByStatus,
          retryReason,
          retryDelayMs,
          statusCode: response.status,
          statusText: response.statusText,
          transcribeModel: this.transcribeModel,
          audioMimeType: attemptAudio.mimeType,
          audioSizeBytes: attemptAudio.data.length,
          audioDurationSeconds: Math.round(attemptAudio.durationSeconds),
          sourceAudioMimeType: audio.mimeType,
          sourceAudioSizeBytes: audio.data.length,
          responseBody
        },
        { scope: "transcription" }
      );

      if (
        !useNormalizedAudio &&
        response.status === 400 &&
        isOpenAIAudioFormatError(responseBody)
      ) {
        const nextNormalized: StoredSessionAudio = normalizedAudio
          ? normalizedAudio
          : await this.normalizeAudioImpl(audio, input.signal);
        normalizedAudio = nextNormalized;
        const normalizationChangedAudio =
          nextNormalized.fileName !== audio.fileName ||
          nextNormalized.mimeType !== audio.mimeType ||
          nextNormalized.data.length !== audio.data.length;
        if (normalizationChangedAudio) {
          logInfo(
            "openai_audio_normalization_recovered_format",
            {
              sessionId: input.sessionId,
              sourceMimeType: audio.mimeType,
              sourceSizeBytes: audio.data.length,
              normalizedMimeType: nextNormalized.mimeType,
              normalizedSizeBytes: nextNormalized.data.length
            },
            { scope: "transcription" }
          );
          useNormalizedAudio = true;
          continue;
        }
      }

      if (
        willRetryByStatus
      ) {
        await delay(retryDelayMs, input.signal);
        continue;
      }

      throw new AIProviderError(
        `OpenAI transcription failed with status ${lastStatus}.`
      );
    }

    throw new AIProviderError(
      `OpenAI transcription failed with status ${lastStatus || "unknown"}.`
    );
  }

  private async synthesizeTranscript(
    input: TranscriptionAdapterInput,
    transcriptSegments: AdapterResponse["transcriptSegments"]
  ): Promise<Omit<AdapterResponse, "transcriptSegments">> {
    const transcriptText = transcriptSegments
      .map(
        (segment) =>
          `[${Math.round(segment.startedAtSeconds)}-${Math.round(segment.endedAtSeconds)}s] ${segment.speakerLabel}: ${segment.text}`
      )
      .join("\n");
    const synthesisUrl = `${this.baseUrl}/responses`;
    for (let attempt = 1; attempt <= OPENAI_TRANSCRIPTION_MAX_ATTEMPTS; attempt += 1) {
      const repairInstruction = attempt > 1
        ? [
            "Previous output had malformed or incomplete JSON.",
            "Return strict valid JSON object only with all required fields."
          ].join(" ")
        : "";
      const response = await this.fetchImpl(synthesisUrl, {
        method: "POST",
        headers: {
          Authorization: `Bearer ${this.apiKey}`,
          "Content-Type": "application/json"
        },
        ...(input.signal ? { signal: input.signal } : {}),
        body: JSON.stringify({
          model: this.synthesisModel,
          input: [
            {
              role: "user",
              content: [
                {
                  type: "input_text",
                  text: [
                    openAITranscriptSynthesisPrompt,
                    repairInstruction,
                    `Today: ${getTodayDateInputValue()}`,
                    `Patient: ${input.context.patientName}`,
                    `Doctor: ${input.context.doctorName}`,
                    "Transcript:",
                    transcriptText
                  ].join("\n\n")
                }
              ]
            }
          ],
          text: {
            format: {
              type: "json_object"
            }
          }
        })
      });

      if (!response.ok) {
        const responseBody = await readProviderErrorBody(response);
        const willRetry =
          attempt < OPENAI_TRANSCRIPTION_MAX_ATTEMPTS &&
          shouldRetryProviderStatus(response.status);
        const retryDelayMs = willRetry
          ? computeRetryDelayMs(OPENAI_TRANSCRIPTION_RETRY_DELAY_MS, attempt)
          : 0;
        logWarn(
          "openai_synthesis_request_failed",
          {
            sessionId: input.sessionId,
            attempt,
            maxAttempts: OPENAI_TRANSCRIPTION_MAX_ATTEMPTS,
            willRetry,
            retryDelayMs,
            retryReason: classifyProviderRetryReason(response.status),
            statusCode: response.status,
            statusText: response.statusText,
            synthesisModel: this.synthesisModel,
            transcriptSegmentCount: transcriptSegments.length,
            responseBody
          },
          { scope: "transcription" }
        );
        if (willRetry) {
          await delay(retryDelayMs, input.signal);
          continue;
        }
        throw new AIProviderError(
          `OpenAI transcript synthesis failed with status ${response.status}.`
        );
      }

      try {
        const payload: unknown = await response.json();
        const text = extractOpenAIResponseText(payload);
        const parsedJson = parseJsonObject(
          text,
          "OpenAI returned malformed JSON for the transcription payload."
        );
        const normalized = normalizeAdapterResponse(
          {
            ...(isRecord(parsedJson) ? parsedJson : {}),
            transcriptSegments
          },
          input
        );
        const parsed = adapterResponseSchema.parse(normalized);
        return {
          summary: parsed.summary,
          metadata: parsed.metadata,
          draftDocument: parsed.draftDocument
        };
      } catch (error) {
        const willRetryMalformed =
          attempt < SYNTHESIS_JSON_REPAIR_MAX_ATTEMPTS &&
          isSynthesisJsonRecoverableError(error);
        logWarn(
          "openai_synthesis_json_invalid",
          {
            sessionId: input.sessionId,
            attempt,
            maxAttempts: SYNTHESIS_JSON_REPAIR_MAX_ATTEMPTS,
            willRetry: willRetryMalformed,
            synthesisModel: this.synthesisModel,
            error: error instanceof Error ? error.message : "Invalid synthesis JSON"
          },
          { scope: "transcription", error }
        );
        if (willRetryMalformed) {
          continue;
        }
        throw new AIProviderError(
          "OpenAI transcript synthesis returned invalid structured output."
        );
      }
    }

    throw new AIProviderError(
      "OpenAI transcript synthesis failed after all retry attempts."
    );
  }
}
