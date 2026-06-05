import { AIProviderError } from "./errors.js";
import { logWarn } from "./logger.js";
import { delay, splitAudioForTranscription, type StoredSessionAudio } from "./transcription-audio-utils.js";
import {
  type AdapterResponse,
  adapterResponseSchema,
  buildAdapterResult,
  classifyProviderRetryReason,
  computeRetryDelayMs,
  extractGeminiText,
  GEMINI_TRANSCRIPTION_MAX_ATTEMPTS,
  GEMINI_TRANSCRIPTION_RETRY_DELAY_MS,
  geminiTranscriptSynthesisPrompt,
  geminiTranscriptionPrompt,
  getTodayDateInputValue,
  isRecord,
  isSynthesisJsonRecoverableError,
  normalizeAdapterResponse,
  parseJsonObject,
  runChunkedTranscription,
  SHARED_CHUNK_TRANSCRIBE_MAX_BYTES,
  shouldRetryProviderStatus,
  SYNTHESIS_JSON_REPAIR_MAX_ATTEMPTS,
  type GeminiAdapterOptions,
  type TranscriptionAdapter,
  type TranscriptionAdapterInput,
  type TranscriptionAdapterResult
} from "./transcription-adapter-shared.js";

export class GeminiTranscriptionAdapter implements TranscriptionAdapter {
  private readonly apiKey: string;
  private readonly model: string;
  private readonly baseUrl: string;
  private readonly fetchImpl: typeof fetch;
  private readonly splitAudioImpl: typeof splitAudioForTranscription;
  readonly providerName: string;

  constructor(options: GeminiAdapterOptions) {
    this.apiKey = options.apiKey;
    this.model = options.model ?? "gemini-2.5-flash";
    this.baseUrl =
      options.baseUrl ?? "https://generativelanguage.googleapis.com/v1beta";
    this.fetchImpl = options.fetchImpl ?? fetch;
    this.splitAudioImpl = options.splitAudioImpl ?? splitAudioForTranscription;
    this.providerName = this.model;
  }

  async transcribe(
    input: TranscriptionAdapterInput
  ): Promise<TranscriptionAdapterResult> {
    if (input.audio.data.length > SHARED_CHUNK_TRANSCRIBE_MAX_BYTES) {
      return this.transcribeChunked(input);
    }

    const parsed = await this.requestAudioTranscription(input, input.audio);

    return buildAdapterResult(this.providerName, parsed, input);
  }

  private async transcribeChunked(
    input: TranscriptionAdapterInput
  ): Promise<TranscriptionAdapterResult> {
    const { mergedSegments, chunkSummaries } = await runChunkedTranscription(input, {
      providerName: this.providerName,
      splitAudioImpl: this.splitAudioImpl,
      requestChunkTranscription: async (chunk) => {
        const parsedChunk = await this.requestAudioTranscription(input, {
          fileName: chunk.fileName,
          mimeType: chunk.mimeType,
          durationSeconds: chunk.durationSeconds,
          data: chunk.data
        });
        return {
          segments: parsedChunk.transcriptSegments,
          summary: parsedChunk.summary,
          alreadyOffsetApplied: false
        };
      }
    });

    const synthesis = await this.synthesizeMergedTranscript(
      input,
      mergedSegments,
      chunkSummaries
    );

    return buildAdapterResult(
      this.providerName,
      {
        ...synthesis,
        transcriptSegments: mergedSegments
      },
      input
    );
  }

  private async requestAudioTranscription(
    input: TranscriptionAdapterInput,
    audio: StoredSessionAudio
  ): Promise<AdapterResponse> {
    let lastStatus = 0;
    let response: Response | null = null;
    for (let attempt = 1; attempt <= GEMINI_TRANSCRIPTION_MAX_ATTEMPTS; attempt += 1) {
      const nextResponse = await this.fetchImpl(
        `${this.baseUrl}/models/${this.model}:generateContent`,
        {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            "x-goog-api-key": this.apiKey
          },
          ...(input.signal ? { signal: input.signal } : {}),
          body: JSON.stringify({
            generationConfig: {
              responseMimeType: "application/json"
            },
            contents: [
              {
                role: "user",
                parts: [
                  {
                    text: [
                      geminiTranscriptionPrompt,
                      `Today: ${getTodayDateInputValue()}`,
                      `Patient: ${input.context.patientName}`,
                      `Doctor: ${input.context.doctorName}`,
                      `Audio filename: ${audio.fileName}`
                    ].join("\n")
                  },
                  {
                    inline_data: {
                      mime_type: audio.mimeType,
                      data: audio.data.toString("base64")
                    }
                  }
                ]
              }
            ]
          })
        }
      );

      if (nextResponse.ok) {
        response = nextResponse;
        break;
      }
      lastStatus = nextResponse.status;
      const retryReason = classifyProviderRetryReason(nextResponse.status);
      const willRetry =
        attempt < GEMINI_TRANSCRIPTION_MAX_ATTEMPTS &&
        shouldRetryProviderStatus(nextResponse.status);
      const retryDelayMs = willRetry
        ? computeRetryDelayMs(GEMINI_TRANSCRIPTION_RETRY_DELAY_MS, attempt)
        : 0;
      logWarn(
        "gemini_transcription_request_failed",
        {
          sessionId: input.sessionId,
          attempt,
          maxAttempts: GEMINI_TRANSCRIPTION_MAX_ATTEMPTS,
          willRetry,
          retryReason,
          retryDelayMs,
          statusCode: nextResponse.status,
          provider: this.providerName
        },
        { scope: "transcription" }
      );
      if (
        willRetry
      ) {
        await delay(retryDelayMs, input.signal);
        continue;
      }
      throw new AIProviderError(
        `Gemini transcription failed with status ${nextResponse.status}.`
      );
    }

    if (!response) {
      throw new AIProviderError(
        `Gemini transcription failed with status ${lastStatus || "unknown"}.`
      );
    }

    const payload: unknown = await response.json();
    const text = extractGeminiText(payload);
    const parsedJson = parseJsonObject(text);
    return adapterResponseSchema.parse(
      normalizeAdapterResponse(parsedJson, {
        ...input,
        audio
      })
    );
  }

  private async synthesizeMergedTranscript(
    input: TranscriptionAdapterInput,
    transcriptSegments: AdapterResponse["transcriptSegments"],
    chunkSummaries: string[]
  ): Promise<AdapterResponse> {
    const transcriptText = transcriptSegments
      .map(
        (segment) =>
          `[${Math.round(segment.startedAtSeconds)}-${Math.round(segment.endedAtSeconds)}s] ${segment.speakerLabel}: ${segment.text}`
      )
      .join("\n");
    let lastStatus = 0;
    let lastSynthesisText = "";
    for (let attempt = 1; attempt <= GEMINI_TRANSCRIPTION_MAX_ATTEMPTS; attempt += 1) {
      const repairInstruction = attempt > 1
        ? [
            "Previous response was malformed or incomplete JSON.",
            "Return valid JSON object only. Include all required fields and arrays."
          ].join(" ")
        : "";
      const nextResponse = await this.fetchImpl(
        `${this.baseUrl}/models/${this.model}:generateContent`,
        {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            "x-goog-api-key": this.apiKey
          },
          ...(input.signal ? { signal: input.signal } : {}),
          body: JSON.stringify({
            generationConfig: {
              responseMimeType: "application/json"
            },
            contents: [
              {
                role: "user",
                parts: [
                  {
                    text: [
                      geminiTranscriptSynthesisPrompt,
                      repairInstruction,
                      `Today: ${getTodayDateInputValue()}`,
                      `Patient: ${input.context.patientName}`,
                      `Doctor: ${input.context.doctorName}`,
                      `Chunk summaries:\n${chunkSummaries.map((summary, index) => `${index + 1}. ${summary}`).join("\n")}`,
                      "Transcript:",
                      transcriptText
                    ].join("\n\n")
                  }
                ]
              }
            ]
          })
        }
      );

      if (nextResponse.ok) {
        const payload: unknown = await nextResponse.json();
        const text = extractGeminiText(payload);
        lastSynthesisText = text;
        try {
          const parsedJson = parseJsonObject(text);
          return adapterResponseSchema.parse(
            normalizeAdapterResponse(
              {
                ...(isRecord(parsedJson) ? parsedJson : {}),
                transcriptSegments
              },
              input
            )
          );
        } catch (error) {
          const shouldRetryMalformed =
            attempt < SYNTHESIS_JSON_REPAIR_MAX_ATTEMPTS &&
            isSynthesisJsonRecoverableError(error);
          logWarn(
            "gemini_synthesis_json_invalid",
            {
              sessionId: input.sessionId,
              attempt,
              maxAttempts: SYNTHESIS_JSON_REPAIR_MAX_ATTEMPTS,
              willRetry: shouldRetryMalformed,
              provider: this.providerName,
              error: error instanceof Error ? error.message : "Invalid synthesis JSON"
            },
            { scope: "transcription", error }
          );
          if (shouldRetryMalformed) {
            continue;
          }
          throw new AIProviderError(
            "Gemini transcript synthesis returned invalid structured output."
          );
        }
      }

      lastStatus = nextResponse.status;
      const retryReason = classifyProviderRetryReason(nextResponse.status);
      const willRetry =
        attempt < GEMINI_TRANSCRIPTION_MAX_ATTEMPTS &&
        shouldRetryProviderStatus(nextResponse.status);
      const retryDelayMs = willRetry
        ? computeRetryDelayMs(GEMINI_TRANSCRIPTION_RETRY_DELAY_MS, attempt)
        : 0;
      logWarn(
        "gemini_synthesis_request_failed",
        {
          sessionId: input.sessionId,
          attempt,
          maxAttempts: GEMINI_TRANSCRIPTION_MAX_ATTEMPTS,
          willRetry,
          retryReason,
          retryDelayMs,
          statusCode: nextResponse.status,
          provider: this.providerName
        },
        { scope: "transcription" }
      );
      if (
        willRetry
      ) {
        await delay(retryDelayMs, input.signal);
        continue;
      }
      throw new AIProviderError(
        `Gemini transcript synthesis failed with status ${nextResponse.status}.`
      );
    }

    throw new AIProviderError(
      lastStatus
        ? `Gemini transcript synthesis failed with status ${lastStatus}.`
        : `Gemini transcript synthesis returned invalid structured output.${lastSynthesisText ? " Please retry." : ""}`
    );
  }
}
