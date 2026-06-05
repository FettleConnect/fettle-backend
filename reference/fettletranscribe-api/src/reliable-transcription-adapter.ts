import { AIProviderError } from "./errors.js";
import { emitKpi } from "./kpi.js";
import { classifyError } from "./logger.js";
import {
  CircuitOpenError,
  runWithReliability,
  type ReliabilityPolicy
} from "./reliability.js";
import {
  type TranscriptionAdapter,
  type TranscriptionAdapterInput,
  type TranscriptionAdapterResult
} from "./transcription-adapter.js";

const DEFAULT_POLICY: Omit<ReliabilityPolicy, "operation"> = {
  timeoutMs: 180_000,
  maxRetries: 2,
  retryDelayMs: 1_000,
  maxFailuresBeforeOpen: 3,
  cooldownMs: 30_000,
  shouldRetry: (error: unknown) => {
    if (error instanceof CircuitOpenError) {
      return false;
    }
    if (error instanceof AIProviderError) {
      const statusCode = parseProviderStatusCode(error.message);
      if (!statusCode) {
        return true;
      }
      if (statusCode >= 500 || statusCode === 408 || statusCode === 429) {
        return true;
      }
      return false;
    }
    return true;
  }
};
const ADAPTIVE_TIMEOUT_PER_AUDIO_MINUTE_MS = 2_500;
const ADAPTIVE_TIMEOUT_PER_MB_MS = 1_000;
const ADAPTIVE_TIMEOUT_MIN_MS = 90_000;
const INTERACTIVE_TIMEOUT_MAX_MS = 420_000;
const BACKGROUND_TIMEOUT_MAX_MS = 900_000;
const LONG_AUDIO_GRACE_THRESHOLD_SECONDS = 20 * 60;
const LONG_AUDIO_EXTRA_GRACE_MS = 90_000;

export class ReliableTranscriptionAdapter implements TranscriptionAdapter {
  readonly providerName: string;
  readonly maxAudioBytes?: number;

  constructor(
    private readonly delegate: TranscriptionAdapter,
    private readonly policy: Partial<Omit<ReliabilityPolicy, "operation">> = {}
  ) {
    this.providerName = delegate.providerName;
    if (delegate.maxAudioBytes !== undefined) {
      this.maxAudioBytes = delegate.maxAudioBytes;
    }
  }

  async transcribe(
    input: TranscriptionAdapterInput
  ): Promise<TranscriptionAdapterResult> {
    const startedAt = Date.now();
    emitKpi(
      "transcription.request",
      "success",
      {
        sessionId: input.sessionId,
        provider: this.providerName
      },
      { scope: "transcription" }
    );

    try {
      const result = await runWithReliability(
        () => this.delegate.transcribe(input),
        {
          operation: `transcription:${this.providerName}`,
          ...DEFAULT_POLICY,
          ...resolvePolicyForExecutionMode(this.policy, input),
          timeoutMs: computeAdaptiveTimeoutMs(
            this.policy.timeoutMs ?? DEFAULT_POLICY.timeoutMs,
            input
          )
        }
      );

      emitKpi(
        "transcription.result",
        "success",
        {
          sessionId: input.sessionId,
          provider: this.providerName,
          elapsedMs: Date.now() - startedAt,
          transcriptSegmentCount: result.transcriptSegments.length,
          requiresRecovery: result.requiresRecovery
        },
        { scope: "transcription" }
      );
      emitProviderHealthKpi(result.provider, "success", {
        sessionId: input.sessionId,
        elapsedMs: Date.now() - startedAt
      });

      return result;
    } catch (error) {
      const errorClass = classifyError(error);
      const retryable = !(error instanceof CircuitOpenError);
      emitKpi(
        "transcription.result",
        "failure",
        {
          sessionId: input.sessionId,
          provider: this.providerName,
          elapsedMs: Date.now() - startedAt,
          error: error instanceof Error ? error.message : "Unknown transcription error",
          errorClass,
          retryable
        },
        { scope: "transcription" }
      );
      emitProviderHealthKpi(this.providerName, "failure", {
        sessionId: input.sessionId,
        elapsedMs: Date.now() - startedAt,
        error: error instanceof Error ? error.message : "Unknown transcription error",
        errorClass
      });

      if (error instanceof Error && /timed out after/i.test(error.message)) {
        emitKpi(
          "transcription.timeout",
          "failure",
          {
            sessionId: input.sessionId,
            provider: this.providerName,
            elapsedMs: Date.now() - startedAt
          },
          { scope: "transcription" }
        );
      }

      if (error instanceof CircuitOpenError) {
        emitKpi(
          "transcription.circuit_open",
          "failure",
          {
            sessionId: input.sessionId,
            provider: this.providerName,
            retryAfterMs: error.retryAfterMs
          },
          { scope: "transcription" }
        );
        throw new AIProviderError(
          `Transcription provider temporarily unavailable. Retry in about ${Math.ceil(error.retryAfterMs / 1000)} seconds.`
        );
      }

      throw error;
    }
  }
}

function computeAdaptiveTimeoutMs(
  configuredTimeoutMs: number,
  input: TranscriptionAdapterInput
): number {
  const durationSeconds = Math.max(0, input.audio.durationSeconds || 0);
  const durationBufferMs =
    Math.ceil(durationSeconds / 60) * ADAPTIVE_TIMEOUT_PER_AUDIO_MINUTE_MS;
  const sizeBufferMs =
    Math.ceil(input.audio.data.length / (1024 * 1024)) *
    ADAPTIVE_TIMEOUT_PER_MB_MS;
  const longAudioGraceMs =
    durationSeconds >= LONG_AUDIO_GRACE_THRESHOLD_SECONDS
      ? LONG_AUDIO_EXTRA_GRACE_MS
      : 0;
  const timeoutMaxMs = input.executionMode === "background"
    ? BACKGROUND_TIMEOUT_MAX_MS
    : INTERACTIVE_TIMEOUT_MAX_MS;
  return Math.min(
    timeoutMaxMs,
    Math.max(
      ADAPTIVE_TIMEOUT_MIN_MS,
      configuredTimeoutMs + durationBufferMs + sizeBufferMs + longAudioGraceMs
    )
  );
}

function resolvePolicyForExecutionMode(
  basePolicy: Partial<Omit<ReliabilityPolicy, "operation">>,
  input: TranscriptionAdapterInput
): Partial<Omit<ReliabilityPolicy, "operation">> {
  if (input.executionMode === "background") {
    return {
      ...basePolicy,
      maxRetries: Math.max(3, basePolicy.maxRetries ?? DEFAULT_POLICY.maxRetries)
    };
  }
  return {
    ...basePolicy,
    maxRetries: Math.min(1, basePolicy.maxRetries ?? DEFAULT_POLICY.maxRetries)
  };
}

function parseProviderStatusCode(message: string): number | null {
  const match = /status\s+(\d{3})/i.exec(message);
  if (!match?.[1]) {
    return null;
  }
  const parsed = Number.parseInt(match[1], 10);
  return Number.isFinite(parsed) ? parsed : null;
}

function emitProviderHealthKpi(
  providerName: string,
  status: "success" | "failure",
  context: {
    sessionId: string;
    elapsedMs?: number;
    error?: string;
    errorClass?: string;
  }
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
      provider: providerName
    },
    { scope: "provider" }
  );
}
