import { logWarn } from "./logger.js";
import { isAbortErrorLike } from "./transcription-audio-utils.js";
import {
  emitProviderHealthKpi,
  shouldFallbackForError,
  type FallbackAdapterOptions,
  type TranscriptionAdapter,
  type TranscriptionAdapterInput,
  type TranscriptionAdapterResult
} from "./transcription-adapter-shared.js";

export class FallbackTranscriptionAdapter implements TranscriptionAdapter {
  readonly providerName: string;

  constructor(private readonly options: FallbackAdapterOptions) {
    this.providerName = `${options.primary.providerName}->${options.fallback.providerName}`;
  }

  async transcribe(
    input: TranscriptionAdapterInput
  ): Promise<TranscriptionAdapterResult> {
    try {
      return await this.options.primary.transcribe(input);
    } catch (error) {
      if (isAbortErrorLike(error)) {
        throw error;
      }
      if (!shouldFallbackForError(error)) {
        throw error;
      }

      logWarn(
        "transcription_primary_provider_failed_falling_back",
        {
          sessionId: input.sessionId,
          primaryProvider: this.options.primary.providerName,
          fallbackProvider: this.options.fallback.providerName,
          error: error instanceof Error ? error.message : "Unknown provider error"
        },
        { scope: "transcription", error }
      );
      emitProviderHealthKpi(this.options.primary.providerName, "failure", {
        sessionId: input.sessionId,
        error: error instanceof Error ? error.message : "Unknown provider error"
      });

      try {
        const fallbackResult = await this.options.fallback.transcribe(input);
        emitProviderHealthKpi(this.options.fallback.providerName, "success", {
          sessionId: input.sessionId
        });
        return fallbackResult;
      } catch (fallbackError) {
        emitProviderHealthKpi(this.options.fallback.providerName, "failure", {
          sessionId: input.sessionId,
          error: fallbackError instanceof Error ? fallbackError.message : "Unknown provider error"
        });
        throw fallbackError;
      }
    }
  }
}
