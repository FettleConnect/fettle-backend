import { type ConsultationSession, sessionIdSchema } from "@medivoice/shared";
import { ZodError, z } from "zod";

import {
  AIProviderError,
  InvalidSessionStateError,
  MalformedAIOutputError,
  NotFoundError,
  UnauthorizedError
} from "./errors.js";
import { emitKpi } from "./kpi.js";
import { classifyError, logError, logInfo, type LogContext } from "./logger.js";
import {
  mergeDraftProvenance,
  mergeDraftWithManualLocks
} from "./session-draft-helpers.js";
import { type SessionRepository } from "./session-repository.js";
import { type TranscriptionAdapter } from "./transcription-adapter.js";

const sessionAudioUploadSchema = z.object({
  fileName: z.string().trim().min(1).max(255),
  mimeType: z.string().trim().startsWith("audio/"),
  sizeBytes: z.int().positive().max(50_000_000),
  durationSeconds: z.number().finite().nonnegative().max(4 * 60 * 60),
  data: z.instanceof(Buffer).refine((value) => value.length > 0, {
    message: "Audio upload payload cannot be empty."
  })
});

export type SessionAudioUpload = z.infer<typeof sessionAudioUploadSchema>;

const maxTranscriptionAttempts = 3;

export class SessionTranscriptionService {
  constructor(
    private readonly repository: SessionRepository,
    private readonly transcriptionAdapter?: TranscriptionAdapter,
    private readonly queueFettleSync?: (
      session: ConsultationSession,
      trigger: "transcription_completed" | "draft_updated"
    ) => void
  ) {}

  async uploadSessionAudio(
    id: string,
    input: SessionAudioUpload,
    doctorId?: string
  ): Promise<ConsultationSession> {
    const sessionId = parseSessionId(id);
    const existingSession = await this.repository.getSessionById(sessionId);

    if (!existingSession) {
      throw new NotFoundError(`Session ${sessionId} was not found.`);
    }

    if (doctorId) {
      assertSessionOwnership(existingSession, doctorId);
    }

    const upload = sessionAudioUploadSchema.parse(input);
    const session = await this.repository.uploadSessionAudio(sessionId, upload);

    if (!session) {
      throw new NotFoundError(`Session ${sessionId} was not found.`);
    }

    return session;
  }

  async submitSessionTranscription(
    id: string,
    doctorId?: string,
    signal?: AbortSignal,
    idempotencyKey?: string
  ): Promise<ConsultationSession> {
    await this.startSessionTranscription(id, doctorId, idempotencyKey);
    return this.processSessionTranscription(id, doctorId, signal, "interactive");
  }

  async requestSessionTranscription(
    id: string,
    doctorId?: string,
    idempotencyKey?: string,
    requestId?: string
  ): Promise<ConsultationSession> {
    const session = await this.startSessionTranscription(
      id,
      doctorId,
      idempotencyKey
    );

    void this.processSessionTranscription(
      id,
      doctorId,
      undefined,
      "interactive"
    ).catch((error) => {
      logError(
        "background_transcription_failed",
        {
          ...(requestId ? { requestId } : {}),
          sessionId: id,
          ...(doctorId ? { doctorId } : {})
        },
        {
          scope: "transcription",
          error
        }
      );
    });

    return session;
  }

  async startSessionTranscription(
    id: string,
    doctorId?: string,
    idempotencyKey?: string
  ): Promise<ConsultationSession> {
    const startedAt = Date.now();
    const preparation = await this.prepareSessionTranscription(id, doctorId, {
      allowProcessingState: true
    });
    if (preparation.session.transcription?.status === "processing") {
      if (
        idempotencyKey &&
        preparation.session.transcription.requestKey === idempotencyKey
      ) {
        return preparation.session;
      }
      throw new InvalidSessionStateError(
        "A transcription is already in progress for this session."
      );
    }
    if (preparation.session.transcription?.exhaustedRetryBudget) {
      throw new InvalidSessionStateError(
        "Transcription retry budget is exhausted for this session. Contact support."
      );
    }

    const priorAttempts = preparation.session.transcription?.attemptCount ?? 0;
    if (priorAttempts >= maxTranscriptionAttempts) {
      throw new InvalidSessionStateError(
        "Transcription retry budget is exhausted for this session. Contact support."
      );
    }
    const requestedAt = new Date().toISOString();

    logTranscriptionEvent("transcription_requested", preparation.baseLogContext);

    const processingSession = await this.repository.markTranscriptionProcessing(
      preparation.sessionId,
      this.transcriptionAdapter!.providerName,
      requestedAt,
      maxTranscriptionAttempts,
      idempotencyKey
    );

    if (!processingSession) {
      throw new NotFoundError(`Session ${preparation.sessionId} was not found.`);
    }

    if (processingSession.transcription?.requestedAt !== requestedAt) {
      if (
        idempotencyKey &&
        processingSession.transcription?.status === "processing" &&
        processingSession.transcription.requestKey === idempotencyKey
      ) {
        return processingSession;
      }
      emitSessionStageKpi("session.transcription.queue", startedAt, "failure", {
        ...preparation.baseLogContext,
        stage: "queue",
        errorClass: "state_conflict",
        retryable: true
      });
      throw new InvalidSessionStateError(
        "A transcription is already in progress for this session."
      );
    }

    logTranscriptionEvent("transcription_queued", preparation.baseLogContext);
    emitSessionStageKpi("session.transcription.queue", startedAt, "success", {
      ...preparation.baseLogContext,
      stage: "queue",
      retryable: false
    });

    return processingSession;
  }

  async processSessionTranscription(
    id: string,
    doctorId?: string,
    signal?: AbortSignal,
    executionMode: "interactive" | "background" = "interactive"
  ): Promise<ConsultationSession> {
    const preparation = await this.prepareSessionTranscription(id, doctorId, {
      allowProcessingState: true
    });
    const startedAt = Date.now();
    let providerCallStartedAt = startedAt;
    const activeRequestKey = preparation.session.transcription?.requestKey;

    if (preparation.session.transcription?.status !== "processing") {
      throw new InvalidSessionStateError(
        "Start transcription before processing it."
      );
    }

    try {
      await this.repository.setTranscriptionStage(
        preparation.sessionId,
        "transcribing_conversation",
        activeRequestKey
      );
      logTranscriptionEvent(
        "transcription_provider_started",
        preparation.baseLogContext
      );
      providerCallStartedAt = Date.now();
      const result = await this.transcriptionAdapter!.transcribe({
        sessionId: preparation.sessionId,
        audio: preparation.audio,
        context: {
          patientName: preparation.session.patient.name,
          doctorName: preparation.session.doctor.name
        },
        executionMode,
        ...(signal ? { signal } : {})
      });
      validateTranscriptionQuality(result, preparation.audio.durationSeconds);
      const mergedDraft = mergeDraftWithManualLocks(
        preparation.session.transcription?.draftDocument,
        preparation.session.transcription?.draftProvenance,
        result.draftDocument
      );
      const mergedProvenance = mergeDraftProvenance(
        preparation.session.transcription?.draftProvenance,
        preparation.session.transcription?.draftDocument,
        mergedDraft,
        "transcript"
      );
      emitSessionStageKpi(
        "session.transcription.provider_call",
        providerCallStartedAt,
        "success",
        {
          ...preparation.baseLogContext,
          stage: "provider_call",
          retryable: false
        }
      );

      await this.repository.setTranscriptionStage(
        preparation.sessionId,
        "double_checking_clarity",
        activeRequestKey
      );

      await this.repository.setTranscriptionStage(
        preparation.sessionId,
        "finalizing_notes",
        activeRequestKey
      );

      const updatedSession = await this.repository.completeTranscription(
        preparation.sessionId,
        {
          ...result,
          draftDocument: mergedDraft,
          draftProvenance: mergedProvenance
        },
        activeRequestKey
      );

      if (!updatedSession) {
        const latestSession = await this.repository.getSessionById(preparation.sessionId);
        if (!latestSession) {
          throw new NotFoundError(`Session ${preparation.sessionId} was not found.`);
        }

        const superseded =
          latestSession.transcription?.status !== "processing" ||
          latestSession.transcription?.requestKey !== activeRequestKey;
        if (superseded) {
          emitKpi(
            "session.transcription.superseded_write_blocked",
            "success",
            {
              ...preparation.baseLogContext,
              stage: "finalize",
              blocked: true
            },
            { scope: "transcription" }
          );
          throw new InvalidSessionStateError(
            "This transcription attempt was superseded by a newer request."
          );
        }

        throw new InvalidSessionStateError(
          "Unable to finalize transcription due to a session state conflict."
        );
      }

      this.queueFettleSync?.(updatedSession, "transcription_completed");
      const speakerQuality = summarizeSpeakerQuality(result.transcriptSegments);

      logTranscriptionEvent("transcription_completed", {
        ...preparation.baseLogContext,
        elapsedMs: Date.now() - startedAt,
        requiresRecovery: result.requiresRecovery,
        transcriptSegmentCount: result.transcriptSegments.length,
        unknownSpeakerCount: speakerQuality.unknownSpeakerCount,
        unknownSpeakerRatioPct: speakerQuality.unknownSpeakerRatioPct,
        speakerAlternationBreakCount: speakerQuality.speakerAlternationBreakCount,
        relabeledSegmentCount: speakerQuality.relabeledSegmentCount
      });
      emitSessionStageKpi(
        "session.transcription.speaker_quality",
        startedAt,
        "success",
        {
          ...preparation.baseLogContext,
          stage: "speaker_quality",
          unknownSpeakerCount: speakerQuality.unknownSpeakerCount,
          unknownSpeakerRatioPct: speakerQuality.unknownSpeakerRatioPct,
          speakerAlternationBreakCount: speakerQuality.speakerAlternationBreakCount,
          relabeledSegmentCount: speakerQuality.relabeledSegmentCount,
          transcriptSegmentCount: result.transcriptSegments.length
        }
      );
      emitSessionStageKpi(
        "session.transcription.end_to_end",
        startedAt,
        "success",
        {
          ...preparation.baseLogContext,
          stage: "end_to_end",
          retryable: false,
          requiresRecovery: result.requiresRecovery,
          transcriptSegmentCount: result.transcriptSegments.length
        }
      );

      return updatedSession;
    } catch (error) {
      const isAbort = isAbortError(error);
      const message = isAbort
        ? "Transcription was canceled before completion."
        : error instanceof Error
          ? error.message
          : "The transcription request failed unexpectedly.";
      const errorClass = classifySessionFailure(error);
      const retryable =
        errorClass === "dependency_ai_provider" ||
        errorClass === "provider_rate_limited" ||
        errorClass === "timeout" ||
        errorClass === "canceled" ||
        errorClass === "unexpected";
      const attemptCount = preparation.session.transcription?.attemptCount ?? 1;
      const exhaustedRetryBudget = retryable && attemptCount >= maxTranscriptionAttempts;

      try {
        await this.repository.failTranscription(preparation.sessionId, {
          provider: this.transcriptionAdapter!.providerName,
          error: exhaustedRetryBudget
            ? `${message} Retry budget exhausted; manual intervention required.`
            : message,
          errorClass,
          retryable,
          exhaustedRetryBudget
        }, activeRequestKey);
      } catch {
      }

      logTranscriptionEvent(
        isAbort ? "transcription_canceled" : "transcription_failed",
        {
          ...preparation.baseLogContext,
          elapsedMs: Date.now() - startedAt,
          error: message,
          errorClass,
          retryable,
          exhaustedRetryBudget
        }
      );
      emitSessionStageKpi(
        "session.transcription.provider_call",
        providerCallStartedAt,
        "failure",
        {
          ...preparation.baseLogContext,
          stage: "provider_call",
          errorClass,
          retryable,
          exhaustedRetryBudget
        }
      );
      emitSessionStageKpi(
        "session.transcription.end_to_end",
        startedAt,
        "failure",
        {
          ...preparation.baseLogContext,
          stage: "end_to_end",
          errorClass,
          retryable,
          exhaustedRetryBudget
        }
      );
      if (exhaustedRetryBudget) {
        emitSessionStageKpi(
          "session.transcription.dead_letter",
          startedAt,
          "failure",
          {
            ...preparation.baseLogContext,
            stage: "dead_letter",
            errorClass,
            retryable: false,
            exhaustedRetryBudget: true
          }
        );
      }

      throw error;
    }
  }

  private async prepareSessionTranscription(
    id: string,
    doctorId?: string,
    options?: { allowProcessingState?: boolean }
  ): Promise<{
    sessionId: string;
    session: ConsultationSession;
    audio: NonNullable<Awaited<ReturnType<SessionRepository["getSessionAudio"]>>>;
    baseLogContext: Record<string, string | number | boolean>;
  }> {
    if (!this.transcriptionAdapter) {
      throw new AIProviderError("No transcription adapter is configured.");
    }

    const sessionId = parseSessionId(id);
    const session = await this.repository.getSessionById(sessionId);

    if (!session) {
      throw new NotFoundError(`Session ${sessionId} was not found.`);
    }

    if (doctorId) {
      assertSessionOwnership(session, doctorId);
    }

    if (!session.audioArtifact) {
      throw new InvalidSessionStateError(
        "Upload consultation audio before starting transcription."
      );
    }

    if (
      !options?.allowProcessingState &&
      session.transcription?.status === "processing"
    ) {
      throw new InvalidSessionStateError(
        "A transcription is already in progress for this session."
      );
    }

    const audio = await this.repository.getSessionAudio(sessionId);

    if (!audio) {
      throw new InvalidSessionStateError(
        "The uploaded consultation audio could not be loaded for transcription."
      );
    }

    if (
      this.transcriptionAdapter.maxAudioBytes &&
      audio.data.length > this.transcriptionAdapter.maxAudioBytes
    ) {
      throw new InvalidSessionStateError(
        "The uploaded consultation audio is too large for the current transcription provider."
      );
    }

    const baseLogContext = {
      sessionId,
      ...(doctorId ? { doctorId } : {}),
      provider: this.transcriptionAdapter.providerName,
      audioSizeBytes: audio.data.length,
      audioDurationSeconds: audio.durationSeconds
    };

    return {
      sessionId,
      session,
      audio,
      baseLogContext
    };
  }
}

function validateTranscriptionQuality(
  result: {
    transcriptSegments: Array<{
      text: string;
      startedAtSeconds: number;
      endedAtSeconds: number;
    }>;
  },
  sourceDurationSeconds: number
): void {
  if (!result.transcriptSegments.length) {
    throw new AIProviderError("Transcript quality check failed: no segments.");
  }

  const nonEmptySegments = result.transcriptSegments.filter(
    (segment) => segment.text.trim().length > 0
  );
  if (nonEmptySegments.length < 2) {
    throw new AIProviderError("Transcript quality check failed: too few usable segments.");
  }

  const uniqueTexts = new Set(
    nonEmptySegments.map((segment) => segment.text.trim().toLowerCase())
  );
  const duplicateRatio = 1 - uniqueTexts.size / nonEmptySegments.length;
  if (duplicateRatio > 0.6) {
    throw new AIProviderError("Transcript quality check failed: duplicate-heavy output.");
  }

  const earliestStart = Math.min(
    ...nonEmptySegments.map((segment) => segment.startedAtSeconds)
  );
  const latestEnd = Math.max(
    ...nonEmptySegments.map((segment) => segment.endedAtSeconds)
  );
  const coveredSeconds = Math.max(0, latestEnd - earliestStart);
  const minimumCoverage = Math.min(
    Math.max(sourceDurationSeconds * 0.2, 45),
    sourceDurationSeconds
  );
  if (coveredSeconds < minimumCoverage) {
    throw new AIProviderError("Transcript quality check failed: low timeline coverage.");
  }
}


function isAbortError(error: unknown): boolean {
  return error instanceof DOMException && error.name === "AbortError";
}

function summarizeSpeakerQuality(
  segments: Array<{ speaker: "doctor" | "patient" | "unknown"; speakerLabel: string }>
): {
  unknownSpeakerCount: number;
  unknownSpeakerRatioPct: number;
  speakerAlternationBreakCount: number;
  relabeledSegmentCount: number;
} {
  if (segments.length === 0) {
    return {
      unknownSpeakerCount: 0,
      unknownSpeakerRatioPct: 0,
      speakerAlternationBreakCount: 0,
      relabeledSegmentCount: 0
    };
  }

  const unknownSpeakerCount = segments.filter((segment) => segment.speaker === "unknown").length;
  const unknownSpeakerRatioPct = Number(((unknownSpeakerCount / segments.length) * 100).toFixed(2));

  let speakerAlternationBreakCount = 0;
  for (let index = 1; index < segments.length; index += 1) {
    const current = segments[index];
    const previous = segments[index - 1];
    if (!current || !previous) {
      continue;
    }
    if (current.speaker === "unknown" || previous.speaker === "unknown") {
      continue;
    }
    if (current.speaker === previous.speaker) {
      speakerAlternationBreakCount += 1;
    }
  }

  const relabeledSegmentCount = segments.filter((segment) => {
    if (segment.speaker === "unknown") {
      return segment.speakerLabel !== "Consultation";
    }
    const normalizedLabel = segment.speakerLabel.trim().toLowerCase();
    return !normalizedLabel.includes(segment.speaker);
  }).length;

  return {
    unknownSpeakerCount,
    unknownSpeakerRatioPct,
    speakerAlternationBreakCount,
    relabeledSegmentCount
  };
}

function classifySessionFailure(error: unknown): string {
  if (error instanceof Error) {
    const message = error.message.toLowerCase();
    if (message.includes("timed out after")) {
      return "timeout";
    }
    if (
      message.includes("rate limit") ||
      message.includes("too many requests") ||
      message.includes("status 429")
    ) {
      return "provider_rate_limited";
    }
  }

  const baseClass = classifyError(error);
  if (baseClass !== "unexpected") {
    return baseClass;
  }

  if (error instanceof InvalidSessionStateError) {
    return "state_conflict";
  }
  if (error instanceof MalformedAIOutputError) {
    return "bad_response";
  }
  if (error instanceof ZodError) {
    return "validation";
  }

  return "unexpected";
}

function emitSessionStageKpi(
  metric: string,
  startedAt: number,
  status: "success" | "failure",
  context: Record<string, string | number | boolean>
): void {
  emitKpi(
    metric,
    status,
    {
      elapsedMs: Date.now() - startedAt,
      ...context
    },
    { scope: "transcription" }
  );
}

function logTranscriptionEvent(
  event: string,
  details: LogContext,
  options?: { error?: unknown }
): void {
  if (options?.error !== undefined) {
    logError(event, details, {
      scope: "transcription",
      error: options.error
    });
    return;
  }

  logInfo(event, details, {
    scope: "transcription"
  });
}

function assertSessionOwnership(
  session: ConsultationSession,
  doctorId: string
): void {
  if (session.doctorId !== doctorId) {
    throw new UnauthorizedError(
      "You do not have access to this consultation session."
    );
  }
}

function parseSessionId(id: string): string {
  try {
    return sessionIdSchema.parse(id);
  } catch (error) {
    if (error instanceof ZodError) {
      throw new NotFoundError(`Session ${id} was not found.`);
    }

    throw error;
  }
}
