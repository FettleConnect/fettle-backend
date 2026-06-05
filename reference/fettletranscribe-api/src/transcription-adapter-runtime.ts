import { randomUUID } from "node:crypto";

import { type TranscriptSegment } from "@medivoice/shared";
import { z } from "zod";

import { AIProviderError, MalformedAIOutputError } from "./errors.js";
import { emitKpi } from "./kpi.js";
import { logInfo, logWarn } from "./logger.js";
import {
  isAbortErrorLike,
  type StoredSessionAudio,
  type splitAudioForTranscription
} from "./transcription-audio-utils.js";
import {
  type AdapterResponse,
  CHUNK_BOUNDARY_WINDOW_SECONDS,
  CHUNK_TRANSCRIPTION_MAX_ATTEMPTS,
  SHARED_CHUNK_TRANSCRIBE_MAX_BYTES,
  TARGET_CHUNK_AUDIO_BYTES,
  type TranscriptionAdapterInput,
  type TranscriptionAdapterResult
} from "./transcription-adapter-types.js";
import {
  inferSpeakerRole,
  postProcessTranscriptSegments
} from "./transcription-adapter-normalization.js";
export async function runChunkedTranscription(
  input: TranscriptionAdapterInput,
  options: {
    providerName: string;
    splitAudioImpl: typeof splitAudioForTranscription;
    requestChunkTranscription: (
      chunk: StoredSessionAudio,
      chunkIndex: number,
      chunkCount: number,
      offsetSeconds: number
    ) => Promise<{
      segments: AdapterResponse["transcriptSegments"];
      summary?: string;
      alreadyOffsetApplied?: boolean;
    }>;
  }
): Promise<{
  mergedSegments: AdapterResponse["transcriptSegments"];
  chunkSummaries: string[];
}> {
  const startedAt = Date.now();
  const chunks = await options.splitAudioImpl(input.audio, {
    maxChunkBytes: SHARED_CHUNK_TRANSCRIBE_MAX_BYTES,
    targetChunkBytes: TARGET_CHUNK_AUDIO_BYTES,
    ...(input.signal ? { signal: input.signal } : {})
  });

  logInfo(
    "transcription_chunking_prepared",
    {
      sessionId: input.sessionId,
      provider: options.providerName,
      sourceSizeBytes: input.audio.data.length,
      sourceDurationSeconds: Math.round(input.audio.durationSeconds),
      chunkCount: chunks.length
    },
    { scope: "transcription" }
  );

  const mergedSegments: AdapterResponse["transcriptSegments"] = [];
  const chunkSummaries: string[] = [];
  const chunkBoundaryOffsets: number[] = [];
  let failedChunkCount = 0;
  let offsetSeconds = 0;

  for (const [index, chunk] of chunks.entries()) {
    logInfo(
      "transcription_chunk_started",
      {
        sessionId: input.sessionId,
        provider: options.providerName,
        chunkIndex: index + 1,
        chunkCount: chunks.length,
        chunkSizeBytes: chunk.data.length,
        chunkDurationSeconds: Math.round(chunk.durationSeconds),
        timelineOffsetSeconds: Math.round(offsetSeconds)
      },
      { scope: "transcription" }
    );

    let chunkOutput:
      | {
          segments: AdapterResponse["transcriptSegments"];
          summary?: string;
          alreadyOffsetApplied?: boolean;
        }
      | null = null;
    let lastChunkError: unknown = null;
    for (let attempt = 1; attempt <= CHUNK_TRANSCRIPTION_MAX_ATTEMPTS; attempt += 1) {
      try {
        chunkOutput = await options.requestChunkTranscription(
          chunk,
          index,
          chunks.length,
          offsetSeconds
        );
        break;
      } catch (error) {
        if (isAbortErrorLike(error)) {
          throw error;
        }
        lastChunkError = error;
        const willRetry = attempt < CHUNK_TRANSCRIPTION_MAX_ATTEMPTS;
        logWarn(
          "transcription_chunk_failed",
          {
            sessionId: input.sessionId,
            provider: options.providerName,
            chunkIndex: index + 1,
            chunkCount: chunks.length,
            attempt,
            maxAttempts: CHUNK_TRANSCRIPTION_MAX_ATTEMPTS,
            willRetry,
            error: error instanceof Error ? error.message : "Unknown chunk error"
          },
          { scope: "transcription", error }
        );
      }
    }

    if (!chunkOutput) {
      failedChunkCount += 1;
      offsetSeconds += chunk.durationSeconds;
      logWarn(
        "transcription_chunk_skipped_after_retries",
        {
          sessionId: input.sessionId,
          provider: options.providerName,
          chunkIndex: index + 1,
          chunkCount: chunks.length,
          failedChunkCount,
          error:
            lastChunkError instanceof Error
              ? lastChunkError.message
              : "Unknown chunk error"
        },
        { scope: "transcription" }
      );
      continue;
    }

    const normalizedSegments = chunkOutput.alreadyOffsetApplied
      ? chunkOutput.segments
      : chunkOutput.segments.map((segment) => ({
          ...segment,
          startedAtSeconds: segment.startedAtSeconds + offsetSeconds,
          endedAtSeconds: segment.endedAtSeconds + offsetSeconds
        }));
    mergedSegments.push(...normalizedSegments);
    if (chunkOutput.summary) {
      chunkSummaries.push(chunkOutput.summary);
    }
    offsetSeconds += chunk.durationSeconds;
    if (index < chunks.length - 1) {
      chunkBoundaryOffsets.push(offsetSeconds);
    }
  }

  if (mergedSegments.length === 0) {
    throw new AIProviderError(
      "Unable to transcribe consultation audio after retrying all audio parts."
    );
  }

  const reconciledSegments = reconcileChunkBoundarySegments(
    mergedSegments,
    chunkBoundaryOffsets
  );

  if (failedChunkCount > 0) {
    logWarn(
      "transcription_chunking_incomplete",
      {
        sessionId: input.sessionId,
        provider: options.providerName,
        chunkCount: chunks.length,
        failedChunkCount,
        successfulChunkCount: chunks.length - failedChunkCount,
        transcriptSegmentCount: reconciledSegments.length
      },
      { scope: "transcription" }
    );
    throw new AIProviderError(
      "Unable to complete transcription for all audio parts. Please retry transcription."
    );
  }

  logInfo(
    "transcription_chunking_completed",
    {
      sessionId: input.sessionId,
      provider: options.providerName,
      chunkCount: chunks.length,
      transcriptSegmentCount: reconciledSegments.length,
      elapsedMs: Date.now() - startedAt
    },
    { scope: "transcription" }
  );

  return {
    mergedSegments: reconciledSegments,
    chunkSummaries
  };
}

function reconcileChunkBoundarySegments(
  segments: AdapterResponse["transcriptSegments"],
  boundaryOffsets: number[]
): AdapterResponse["transcriptSegments"] {
  const sorted = [...segments].sort(
    (a, b) => a.startedAtSeconds - b.startedAtSeconds
  );
  if (sorted.length <= 1 || boundaryOffsets.length === 0) {
    return sorted;
  }

  const deduped: AdapterResponse["transcriptSegments"] = [];
  for (const segment of sorted) {
    const previous = deduped[deduped.length - 1];
    if (
      previous &&
      isLikelyBoundaryDuplicate(previous, segment, boundaryOffsets)
    ) {
      const previousDuration = previous.endedAtSeconds - previous.startedAtSeconds;
      const nextDuration = segment.endedAtSeconds - segment.startedAtSeconds;
      const keepNext =
        nextDuration > previousDuration ||
        normalizeBoundaryText(segment.text).length >
          normalizeBoundaryText(previous.text).length;
      if (keepNext) {
        deduped[deduped.length - 1] = segment;
      }
      continue;
    }
    deduped.push(segment);
  }

  for (let index = 1; index < deduped.length - 1; index += 1) {
    const current = deduped[index];
    if (!current || current.speaker !== "unknown") {
      continue;
    }
    const nearBoundary = boundaryOffsets.some(
      (boundary) =>
        Math.abs(current.startedAtSeconds - boundary) <=
          CHUNK_BOUNDARY_WINDOW_SECONDS ||
        Math.abs(current.endedAtSeconds - boundary) <=
          CHUNK_BOUNDARY_WINDOW_SECONDS
    );
    if (!nearBoundary) {
      continue;
    }
    const previous = deduped[index - 1];
    const next = deduped[index + 1];
    if (
      previous &&
      next &&
      previous.speaker === next.speaker &&
      previous.speaker !== "unknown"
    ) {
      current.speaker = previous.speaker;
      current.speakerLabel = previous.speakerLabel;
    }
  }

  return deduped;
}

function isLikelyBoundaryDuplicate(
  previous: AdapterResponse["transcriptSegments"][number],
  next: AdapterResponse["transcriptSegments"][number],
  boundaries: number[]
): boolean {
  const nearBoundary = boundaries.some((boundary) => {
    return (
      Math.abs(previous.endedAtSeconds - boundary) <=
        CHUNK_BOUNDARY_WINDOW_SECONDS &&
      Math.abs(next.startedAtSeconds - boundary) <= CHUNK_BOUNDARY_WINDOW_SECONDS
    );
  });
  if (!nearBoundary) {
    return false;
  }

  const prevText = normalizeBoundaryText(previous.text);
  const nextText = normalizeBoundaryText(next.text);
  if (!prevText || !nextText) {
    return false;
  }
  if (prevText === nextText) {
    return true;
  }
  return prevText.includes(nextText) || nextText.includes(prevText);
}

function normalizeBoundaryText(value: string): string {
  return value.toLowerCase().replace(/[^\w\s]/g, "").replace(/\s+/g, " ").trim();
}

export function shouldFallbackForError(error: unknown): boolean {
  if (!(error instanceof AIProviderError)) {
    return true;
  }

  const statusCode = parseProviderStatusCode(error.message);
  if (!statusCode) {
    return true;
  }

  return shouldRetryProviderStatus(statusCode);
}

export function parseProviderStatusCode(message: string): number | null {
  const match = /status\s+(\d{3})/i.exec(message);
  if (!match?.[1]) {
    return null;
  }
  const parsed = Number.parseInt(match[1], 10);
  return Number.isFinite(parsed) ? parsed : null;
}

export function emitProviderHealthKpi(
  providerName: string,
  status: "success" | "failure",
  context: { sessionId: string; error?: string }
): void {
  const normalized = providerName.toLowerCase();
  const metric = normalized.includes("openai") || normalized.includes("gpt-")
    ? "provider.openai.health"
    : normalized.includes("gemini") || normalized.includes("mock-gemini")
      ? "provider.gemini.health"
      : undefined;

  if (!metric) {
    return;
  }

  emitKpi(
    metric,
    status,
    {
      ...context,
      provider: providerName,
      ...(context.error ? { errorClass: "dependency_ai_provider" } : {})
    },
    { scope: "provider" }
  );
}

export function shouldRetryProviderStatus(statusCode: number): boolean {
  return statusCode === 408 || statusCode === 429 || statusCode >= 500;
}

export function isSynthesisJsonRecoverableError(error: unknown): boolean {
  if (error instanceof MalformedAIOutputError) {
    return true;
  }
  if (error instanceof Error && error.name === "ZodError") {
    return true;
  }
  return false;
}

export function classifyProviderRetryReason(statusCode: number): string {
  if (statusCode === 408) {
    return "upstream_timeout";
  }
  if (statusCode === 429) {
    return "rate_limited";
  }
  if (statusCode >= 500) {
    return "provider_unavailable";
  }
  if (statusCode >= 400) {
    return "client_error";
  }
  return "unknown";
}

export function computeRetryDelayMs(baseDelayMs: number, attempt: number): number {
  if (process.env.NODE_ENV === "test") {
    return Math.min(20, Math.max(1, baseDelayMs));
  }
  const exponent = Math.max(0, attempt - 1);
  const cappedExponential = Math.min(baseDelayMs * 2 ** exponent, 8_000);
  const jitter = Math.floor(Math.random() * 250);
  return cappedExponential + jitter;
}

export function buildAdapterResult(
  providerName: string,
  parsed: AdapterResponse,
  input: TranscriptionAdapterInput
): TranscriptionAdapterResult {
  const normalizedSegments = postProcessTranscriptSegments(
    parsed.transcriptSegments,
    input
  );
  const requiresRecovery = detectUnifiedRecoveryRequired(
    parsed.summary,
    normalizedSegments,
    parsed.metadata.careTeamNotes
  );

  return {
    provider: providerName,
    requiresRecovery,
    summary: parsed.summary,
    transcriptSegments: normalizedSegments.map((segment) => ({
      ...segment,
      id: randomUUID()
    })),
    metadata: parsed.metadata,
    draftDocument: parsed.draftDocument
  };
}

function detectRecoveryRequired(
  summary: string,
  transcriptSegments: Array<Pick<TranscriptSegment, "text">>
): boolean {
  const normalizedSummary = summary.toLowerCase();
  const normalizedTranscript = transcriptSegments
    .map((segment) => segment.text.toLowerCase())
    .join(" ");

  const recoverySignals = [
    "no discernible speech",
    "transcript content was incomplete",
    "cannot be generated",
    "only background noise",
    "retry transcription"
  ];

  return recoverySignals.some(
    (signal) =>
      normalizedSummary.includes(signal) || normalizedTranscript.includes(signal)
  );
}

function detectUnifiedRecoveryRequired(
  summary: string,
  transcriptSegments: Array<Pick<TranscriptSegment, "speaker" | "text">>,
  careTeamNotes: string[]
): boolean {
  if (detectRecoveryRequired(summary, transcriptSegments)) {
    return true;
  }
  const combined = [
    summary,
    ...careTeamNotes,
    ...transcriptSegments.map((segment) => segment.text)
  ]
    .join(" ")
    .toLowerCase();
  const nativeScriptSegments = transcriptSegments.filter((segment) =>
    /[\u0900-\u097F\u0C00-\u0C7F]/.test(segment.text)
  ).length;
  const unknownSpeakerRatio =
    transcriptSegments.length === 0
      ? 1
      : transcriptSegments.filter((segment) => segment.speaker === "unknown")
          .length / transcriptSegments.length;

  return (
    /\b(too garbled|garbled|not a usable medical consultation|not contain a clinical consultation|non-medical|no clinical information|no clear patient-specific|unrelated)\b/i.test(
      combined
    ) ||
    detectSingleSpeakerDialogueBlob(transcriptSegments) ||
    nativeScriptSegments >= 2 ||
    unknownSpeakerRatio >= 0.85
  );
}

function detectSingleSpeakerDialogueBlob(
  transcriptSegments: Array<Pick<TranscriptSegment, "speaker" | "text">>
): boolean {
  if (transcriptSegments.length === 0 || transcriptSegments.length > 3) {
    return false;
  }

  const nonEmptySegments = transcriptSegments
    .map((segment) => ({
      speaker: segment.speaker,
      text: segment.text.trim()
    }))
    .filter((segment) => segment.text.length > 0);

  if (nonEmptySegments.length === 0) {
    return false;
  }

  const uniqueSpeakers = new Set(nonEmptySegments.map((segment) => segment.speaker));
  if (uniqueSpeakers.size !== 1) {
    return false;
  }

  const longestSegmentLength = Math.max(
    ...nonEmptySegments.map((segment) => segment.text.length)
  );
  if (longestSegmentLength < 220) {
    return false;
  }

  const combined = nonEmptySegments
    .map((segment) => segment.text.toLowerCase())
    .join(" ");
  const cueMatches = [
    /\bhow are you\b/i,
    /\bwhat brings you here\b/i,
    /\bi'?m fine\b/i,
    /\bokay doctor\b/i,
    /\bthank you\b/i,
    /\bwelcome\b/i
  ].filter((pattern) => pattern.test(combined)).length;

  const questionCount = (combined.match(/\?/g) ?? []).length;

  return cueMatches >= 3 && questionCount >= 2;
}

export function extractGeminiText(payload: unknown): string {
  const schema = z.object({
    candidates: z
      .array(
        z.object({
          content: z.object({
            parts: z.array(
              z.object({
                text: z.string().optional()
              })
            )
          })
        })
      )
      .min(1)
  });

  const parsed = schema.safeParse(payload);

  if (!parsed.success) {
    throw new AIProviderError("Gemini returned an unexpected response shape.");
  }

  const text = parsed.data.candidates[0]?.content.parts
    .map((part) => part.text?.trim())
    .filter((value): value is string => Boolean(value))
    .join("\n");

  if (!text) {
    throw new MalformedAIOutputError("Gemini returned an empty response.");
  }

  return text;
}

export function parseJsonObject(
  value: string,
  errorMessage = "Gemini returned malformed JSON for the transcription payload."
): unknown {
  const normalized = value.replace(/^```json\s*/i, "").replace(/```$/i, "").trim();

  try {
    return JSON.parse(normalized);
  } catch {
    throw new MalformedAIOutputError(errorMessage);
  }
}

export function extractOpenAITranscriptSegments(
  payload: unknown,
  input: TranscriptionAdapterInput,
  offsetSeconds: number
): AdapterResponse["transcriptSegments"] {
  if (!isRecord(payload)) {
    throw new AIProviderError("OpenAI returned an unexpected transcription shape.");
  }

  const rawSegments = Array.isArray(payload.segments)
    ? payload.segments
    : Array.isArray(payload.words)
      ? payload.words
      : [];

  if (rawSegments.length === 0) {
    const text = normalizeString(payload.text);

    if (!text) {
      throw new MalformedAIOutputError("OpenAI returned an empty transcript.");
    }

    return [
      {
        speaker: "unknown",
        speakerLabel: "Consultation",
        text,
        startedAtSeconds: offsetSeconds,
        endedAtSeconds:
          offsetSeconds + Math.max(1, Math.round(input.audio.durationSeconds || 1))
      }
    ];
  }

  const segments = rawSegments
    .map((segment, index) => {
      if (!isRecord(segment)) {
        return null;
      }

      const text = normalizeString(segment.text ?? segment.word);

      if (!text) {
        return null;
      }

      const speakerLabel =
        normalizeString(segment.speaker) ||
        normalizeString(segment.speaker_label) ||
        normalizeString(segment.speakerLabel) ||
        "Speaker";
      const startedAtSeconds =
        normalizeSeconds(segment.start ?? segment.startedAtSeconds, index) +
        offsetSeconds;
      const endedAtSeconds = Math.max(
        startedAtSeconds,
        normalizeSeconds(
          segment.end ?? segment.endedAtSeconds,
          index + 1
        ) + offsetSeconds
      );

      return {
        speaker: inferSpeakerRole(speakerLabel, text, input),
        speakerLabel,
        text,
        startedAtSeconds,
        endedAtSeconds
      };
    })
    .filter((segment): segment is AdapterResponse["transcriptSegments"][number] =>
      Boolean(segment)
    );

  if (segments.length === 0) {
    throw new MalformedAIOutputError("OpenAI returned an empty transcript.");
  }

  return segments;
}

export function extractOpenAIResponseText(payload: unknown): string {
  if (!isRecord(payload)) {
    throw new AIProviderError("OpenAI returned an unexpected response shape.");
  }

  const outputText = normalizeString(payload.output_text);

  if (outputText) {
    return outputText;
  }

  if (Array.isArray(payload.output)) {
    const text = payload.output
      .flatMap((item) =>
        isRecord(item) && Array.isArray(item.content) ? item.content : []
      )
      .map((content) =>
        isRecord(content)
          ? normalizeString(content.text ?? content.output_text)
          : ""
      )
      .filter(Boolean)
      .join("\n");

    if (text) {
      return text;
    }
  }

  throw new MalformedAIOutputError("OpenAI returned an empty response.");
}

export function isOpenAIAudioFormatError(responseBody: string): boolean {
  const normalized = responseBody.toLowerCase();
  return (
    normalized.includes("corrupted") ||
    normalized.includes("unsupported") ||
    normalized.includes("invalid_value") ||
    normalized.includes("audio file")
  );
}

export function shouldPreNormalizeOpenAIAudio(audio: StoredSessionAudio): boolean {
  const mimeType = (audio.mimeType || "").toLowerCase();
  const fileName = (audio.fileName || "").toLowerCase();
  const riskyMimeType = mimeType.includes("ogg") ||
    mimeType.includes("opus");
  const riskyExtension = fileName.endsWith(".ogg") ||
    fileName.endsWith(".opus") ||
    fileName.endsWith(".oga");
  const largePayload = audio.data.length > 10_000_000;
  return riskyMimeType || riskyExtension || largePayload;
}

function normalizeString(value: unknown): string | undefined {
  if (typeof value !== "string") {
    return undefined;
  }

  const trimmed = value.trim();
  return trimmed.length > 0 ? trimmed : undefined;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

function normalizeSeconds(value: unknown, fallback: number): number {
  if (typeof value === "number" && Number.isFinite(value)) {
    return Math.max(0, value);
  }

  if (typeof value === "string") {
    const parsed = Number(value);

    if (Number.isFinite(parsed)) {
      return Math.max(0, parsed);
    }
  }

  return fallback;
}
