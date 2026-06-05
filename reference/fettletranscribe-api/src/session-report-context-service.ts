import {
  type ConsultationSession,
  type DraftConsultationDocument,
  type ReportContextChunk,
  type ReportContextExtractionJob,
  sessionIdSchema
} from "@medivoice/shared";
import { PDFDocument } from "pdf-lib";
import { ZodError, z } from "zod";

import {
  InvalidSessionStateError,
  NotFoundError,
  UnauthorizedError
} from "./errors.js";
import { emitKpi } from "./kpi.js";
import { logError } from "./logger.js";
import {
  ReportContextPartialFailureError,
  type ReportContextAdapter
} from "./report-context-service.js";
import {
  hasDraftContentChanged,
  mergeDraftProvenance
} from "./session-draft-helpers.js";
import { type SessionRepository } from "./session-repository.js";
import { DocumentService } from "./document-service.js";

const allowedReportMimeTypes = new Set([
  "application/pdf",
  "image/jpeg",
  "image/png",
  "image/webp"
]);
const maxReportUploadBytes = 15_000_000;
const reportUploadTtlMs = 24 * 60 * 60 * 1000;

const reportContextUploadSchema = z.object({
  fileName: z.string().trim().min(1).max(255),
  mimeType: z.string().trim().min(1),
  sizeBytes: z.int().positive().max(maxReportUploadBytes),
  data: z.instanceof(Buffer).refine((value) => value.length > 0, {
    message: "Report upload payload cannot be empty."
  })
}).superRefine((value, context) => {
  if (!allowedReportMimeTypes.has(value.mimeType)) {
    context.addIssue({
      code: z.ZodIssueCode.custom,
      path: ["mimeType"],
      message: "Report uploads must be PDF, JPEG, PNG, or WebP."
    });
  }
});

export type ReportContextUpload = z.infer<typeof reportContextUploadSchema>;

export class SessionReportContextService {
  constructor(
    private readonly repository: SessionRepository,
    private readonly documentService: DocumentService,
    private readonly reportContextAdapter: ReportContextAdapter,
    private readonly queueFettleSync?: (
      session: ConsultationSession,
      trigger: "transcription_completed" | "draft_updated"
    ) => void
  ) {}

  async uploadReportContextFile(
    id: string,
    input: ReportContextUpload,
    doctorId?: string
  ): Promise<{
    id: string;
    fileName: string;
    mimeType: string;
    sizeBytes: number;
    expiresAt: string;
  }> {
    const session = await this.getOwnedSession(id, doctorId);

    if (session.transcription?.status !== "completed") {
      throw new InvalidSessionStateError(
        "Complete transcription before adding report context."
      );
    }

    const upload = reportContextUploadSchema.parse(input);
    const stored = await this.repository.storeReportContextUpload(session.id, {
      ...upload,
      expiresAt: new Date(Date.now() + reportUploadTtlMs).toISOString()
    });

    return {
      id: stored.id,
      fileName: stored.fileName,
      mimeType: stored.mimeType,
      sizeBytes: stored.sizeBytes,
      expiresAt: stored.expiresAt
    };
  }

  async deleteReportContextUpload(
    id: string,
    uploadId: string,
    doctorId?: string
  ): Promise<void> {
    const session = await this.getOwnedSession(id, doctorId);
    await this.repository.deleteReportContextUpload(session.id, uploadId);
  }

  async listReportContextUploads(
    id: string,
    doctorId?: string
  ): Promise<
    Array<{
      id: string;
      fileName: string;
      mimeType: string;
      sizeBytes: number;
      expiresAt: string;
    }>
  > {
    const session = await this.getOwnedSession(id, doctorId);
    const uploads = await this.repository.getReportContextUploads(session.id);
    return uploads.map((upload) => ({
      id: upload.id,
      fileName: upload.fileName,
      mimeType: upload.mimeType,
      sizeBytes: upload.sizeBytes,
      expiresAt: upload.expiresAt
    }));
  }

  async getReportContextUploadContent(
    id: string,
    uploadId: string,
    doctorId?: string
  ): Promise<{
    fileName: string;
    mimeType: string;
    data: Buffer;
  }> {
    const session = await this.getOwnedSession(id, doctorId);
    const uploads = await this.repository.getReportContextUploads(session.id, [uploadId]);
    const upload = uploads[0];
    if (!upload) {
      throw new NotFoundError("Report upload not found.");
    }

    return {
      fileName: upload.fileName,
      mimeType: upload.mimeType,
      data: upload.data
    };
  }

  async extractReportContext(
    id: string,
    uploadIds: string[],
    doctorId?: string,
    options?: { jobId?: string }
  ): Promise<ConsultationSession> {
    const session = await this.getOwnedSession(id, doctorId);

    if (session.transcription?.status !== "completed") {
      throw new InvalidSessionStateError(
        "Complete transcription before extracting report context."
      );
    }

    const uniqueUploadIds = [...new Set(uploadIds)];
    const existingContext = session.reportContext;
    const previouslyExtractedUploadIds = existingContext?.extractedUploadIds ?? [];
    const nextUploadIds = uniqueUploadIds.filter(
      (uploadId) => !previouslyExtractedUploadIds.includes(uploadId)
    );
    if (nextUploadIds.length === 0) {
      throw new InvalidSessionStateError(
        "No new report files to extract. Add new files before extracting again."
      );
    }
    const uploads = await this.repository.getReportContextUploads(session.id, nextUploadIds);

    if (uploads.length !== nextUploadIds.length) {
      throw new NotFoundError("One or more report uploads could not be loaded.");
    }

    const now = new Date().toISOString();
    if (options?.jobId) {
      await this.assertReportContextJobIsActive(session.id, options.jobId);
    }
    const totalUnitsEstimate = await estimateReportContextUnits(uploads);
    const initialProgress = {
      totalUnits: totalUnitsEstimate,
      completedUnits: 0,
      failedUnits: 0,
      startedAt: now,
      updatedAt: now,
      ...(totalUnitsEstimate > 0
        ? {
            etaSecondsLower: totalUnitsEstimate * 4,
            etaSecondsUpper: totalUnitsEstimate * 18
          }
        : {}),
      currentStage: "Preparing report pages"
    };
    const processingSession = await this.repository.updateReportContext(session.id, {
      status: "processing",
      chunks: existingContext?.chunks ?? [],
      selectedChunkIds: existingContext?.selectedChunkIds ?? [],
      appliedChunkIds: existingContext?.appliedChunkIds ?? [],
      extractedUploadIds: previouslyExtractedUploadIds,
      progress: initialProgress,
      updatedAt: now
    });

    if (!processingSession) {
      throw new NotFoundError(`Session ${session.id} was not found.`);
    }

    try {
      const chunks = await this.reportContextAdapter.extractChunks({
        sessionId: session.id,
        uploads,
        onUnitProgress: async (progress) => {
          if (options?.jobId) {
            await this.assertReportContextJobIsActive(session.id, options.jobId);
          }
          const updatedAt = new Date().toISOString();
          await this.repository.updateReportContext(session.id, {
            status: "processing",
            chunks: [...(existingContext?.chunks ?? []), ...progress.extractedChunks],
            selectedChunkIds: existingContext?.selectedChunkIds ?? [],
            appliedChunkIds: existingContext?.appliedChunkIds ?? [],
            extractedUploadIds: previouslyExtractedUploadIds,
            progress: {
              totalUnits: progress.totalUnits,
              completedUnits: progress.completedUnits,
              failedUnits: progress.failedUnits,
              startedAt: now,
              updatedAt,
              ...estimateEtaBand(progress),
              currentStage:
                progress.completedUnits + progress.failedUnits >= progress.totalUnits
                  ? "Finalizing results"
                  : "Extracting report content"
            },
            updatedAt
          });
          if (options?.jobId) {
            await this.repository.updateReportContextExtractionJob(session.id, options.jobId, {
              status: "processing",
              chunks: progress.extractedChunks,
              totalUnits: progress.totalUnits,
              completedUnits: progress.completedUnits,
              failedUnits: progress.failedUnits,
              updatedAt
            });
          }
        }
      });
      const nextSession = await this.repository.updateReportContext(session.id, {
        status: "ready",
        chunks: [...(existingContext?.chunks ?? []), ...chunks],
        selectedChunkIds: [],
        appliedChunkIds: existingContext?.appliedChunkIds ?? [],
        extractedUploadIds: [
          ...new Set([...previouslyExtractedUploadIds, ...nextUploadIds])
        ],
        progress: {
          totalUnits: totalUnitsEstimate,
          completedUnits: totalUnitsEstimate,
          failedUnits: 0,
          startedAt: now,
          updatedAt: new Date().toISOString(),
          etaSecondsLower: 0,
          etaSecondsUpper: 0,
          currentStage: "Completed"
        },
        updatedAt: new Date().toISOString()
      });

      if (!nextSession) {
        throw new NotFoundError(`Session ${session.id} was not found.`);
      }
      if (options?.jobId) {
        await this.assertReportContextJobIsActive(session.id, options.jobId);
        await this.repository.updateReportContextExtractionJob(session.id, options.jobId, {
          status: "ready",
          chunks,
          totalUnits: totalUnitsEstimate,
          completedUnits: totalUnitsEstimate,
          failedUnits: 0,
          finishedAt: new Date().toISOString(),
          updatedAt: new Date().toISOString()
        });
      }

      try {
        await Promise.all(
          nextUploadIds.map((uploadId) =>
            this.repository.deleteReportContextUpload(session.id, uploadId)
          )
        );
      } catch (cleanupError) {
        logError(
          "report_context_upload_cleanup_failed",
          {
            sessionId: session.id,
            uploadCount: nextUploadIds.length
          },
          { error: cleanupError }
        );
      }

      return nextSession;
    } catch (error) {
      const isCanceled = error instanceof InvalidSessionStateError;
      const partialFailure =
        error instanceof ReportContextPartialFailureError ? error : null;
      const failedUnits = partialFailure?.failedUnits ?? totalUnitsEstimate;
      const completedUnits = partialFailure?.completedUnits ?? 0;
      const preservedChunks = partialFailure
        ? [...(existingContext?.chunks ?? []), ...partialFailure.partialChunks]
        : (existingContext?.chunks ?? []);
      await this.repository.updateReportContext(session.id, {
        status: "failed",
        chunks: preservedChunks,
        selectedChunkIds: existingContext?.selectedChunkIds ?? [],
        appliedChunkIds: existingContext?.appliedChunkIds ?? [],
        extractedUploadIds: previouslyExtractedUploadIds,
        progress: {
          totalUnits: totalUnitsEstimate,
          completedUnits,
          failedUnits,
          startedAt: now,
          updatedAt: new Date().toISOString(),
          currentStage: "Failed"
        },
        updatedAt: new Date().toISOString(),
        error: isCanceled
          ? "Report extraction was canceled."
          : partialFailure
            ? "Some report pages could not be read. Review preview items and retry for complete extraction."
            : error instanceof Error
              ? error.message
              : "Report extraction failed."
      });
      if (options?.jobId) {
        await this.repository.updateReportContextExtractionJob(session.id, options.jobId, {
          status: isCanceled ? "canceled" : "failed",
          chunks: partialFailure?.partialChunks ?? [],
          totalUnits: partialFailure?.totalUnits ?? totalUnitsEstimate,
          completedUnits,
          failedUnits,
          error: isCanceled
            ? "Report extraction was canceled."
            : partialFailure
              ? "Some report pages could not be read. Review preview items and retry for complete extraction."
              : error instanceof Error
                ? error.message
                : "Report extraction failed.",
          updatedAt: new Date().toISOString(),
          finishedAt: new Date().toISOString()
        });
      }
      throw error;
    }
  }

  async startReportContextExtractionJob(
    id: string,
    uploadIds: string[],
    doctorId?: string
  ): Promise<ReportContextExtractionJob> {
    const session = await this.getOwnedSession(id, doctorId);

    const existingActive = await this.repository.findActiveReportContextExtractionJob(
      session.id
    );
    if (existingActive) {
      return existingActive;
    }

    const uniqueUploadIds = [...new Set(uploadIds)];
    const previouslyExtractedUploadIds = session.reportContext?.extractedUploadIds ?? [];
    const nextUploadIds = uniqueUploadIds.filter(
      (uploadId) => !previouslyExtractedUploadIds.includes(uploadId)
    );
    if (nextUploadIds.length === 0) {
      throw new InvalidSessionStateError(
        "No new report files to extract. Add new files before extracting again."
      );
    }
    const job = await this.repository.createReportContextExtractionJob({
      sessionId: session.id,
      uploadIds: nextUploadIds
    });
    await this.repository.updateReportContextExtractionJob(session.id, job.id, {
      status: "processing",
      startedAt: new Date().toISOString(),
      updatedAt: new Date().toISOString()
    });

    void this.extractReportContext(session.id, nextUploadIds, doctorId, {
      jobId: job.id
    }).catch((error) => {
      logError(
        "report_context_job_failed",
        { sessionId: session.id, jobId: job.id },
        { scope: "report_context", error }
      );
    });

    const latest = await this.repository.getReportContextExtractionJob(session.id, job.id);
    if (!latest) {
      throw new NotFoundError(`Report context job ${job.id} was not found.`);
    }
    return latest;
  }

  async getReportContextExtractionJob(
    id: string,
    jobId: string,
    doctorId?: string
  ): Promise<ReportContextExtractionJob> {
    const session = await this.getOwnedSession(id, doctorId);
    const job = await this.repository.getReportContextExtractionJob(session.id, jobId);
    if (!job) {
      throw new NotFoundError(`Report context job ${jobId} was not found.`);
    }
    return job;
  }

  async cancelReportContextExtractionJob(
    id: string,
    jobId: string,
    doctorId?: string
  ): Promise<ReportContextExtractionJob> {
    const session = await this.getOwnedSession(id, doctorId);
    const job = await this.repository.getReportContextExtractionJob(session.id, jobId);
    if (!job) {
      throw new NotFoundError(`Report context job ${jobId} was not found.`);
    }
    if (job.status === "ready" || job.status === "failed" || job.status === "canceled") {
      return job;
    }
    const now = new Date().toISOString();
    const canceled = await this.repository.updateReportContextExtractionJob(
      session.id,
      jobId,
      {
        status: "canceled",
        error: "Report extraction was canceled.",
        updatedAt: now,
        finishedAt: now
      }
    );
    if (!canceled) {
      throw new NotFoundError(`Report context job ${jobId} was not found.`);
    }
    emitKpi(
      "report_context.job.canceled",
      "success",
      { sessionId: session.id, jobId },
      { scope: "report_context" }
    );
    return canceled;
  }

  async recoverStaleReportContextExtractionJobs(staleAfterMs: number): Promise<number> {
    return this.repository.recoverStaleReportContextExtractionJobs(staleAfterMs);
  }

  async suggestReportContextDraft(
    id: string,
    chunkIds: string[],
    customChunks: ReportContextChunk[] | undefined,
    doctorId?: string
  ): Promise<{
    draftDocument: DraftConsultationDocument;
    changedFields: string[];
  }> {
    const session = await this.getOwnedSession(id, doctorId);

    if (!session.transcription?.draftDocument) {
      throw new InvalidSessionStateError(
        "Run transcription before applying report context."
      );
    }
    if (session.reportContext?.status !== "ready") {
      throw new InvalidSessionStateError(
        "Complete report extraction successfully before generating suggestions."
      );
    }

    const selectedChunks = selectReportContextChunks(
      session.reportContext?.chunks ?? [],
      chunkIds,
      customChunks
    );
    const suggestion = await this.reportContextAdapter.suggestDraft({
      currentDraft: session.transcription.draftDocument,
      selectedChunks
    });

    return {
      draftDocument: this.documentService.normalizeDraftDocument(suggestion.draftDocument),
      changedFields: suggestion.changedFields
    };
  }

  async applyReportContextDraft(
    id: string,
    chunkIds: string[],
    customChunks: ReportContextChunk[] | undefined,
    draftDocument: DraftConsultationDocument,
    doctorId?: string
  ): Promise<ConsultationSession> {
    const session = await this.getOwnedSession(id, doctorId);
    if (session.reportContext?.status !== "ready") {
      throw new InvalidSessionStateError(
        "Complete report extraction successfully before applying report context."
      );
    }
    const selectedChunks = selectReportContextChunks(
      session.reportContext?.chunks ?? [],
      chunkIds,
      customChunks
    );
    const normalizedDraft = this.documentService.normalizeDraftDocument(draftDocument);
    const draftChanged = hasDraftContentChanged(
      session.transcription?.draftDocument,
      normalizedDraft
    );
    const updatedDraftSession = await this.repository.updateDraftDocument(
      session.id,
      normalizedDraft,
      mergeDraftProvenance(
        session.transcription?.draftProvenance,
        session.transcription?.draftDocument,
        normalizedDraft,
        "report_context"
      )
    );

    if (!updatedDraftSession) {
      throw new NotFoundError(`Session ${session.id} was not found.`);
    }

    const nextSession = await this.repository.updateReportContext(session.id, {
      status: "ready",
      chunks: session.reportContext?.chunks ?? [],
      selectedChunkIds: selectedChunks.map((chunk) => chunk.id),
      appliedChunkIds: [
        ...new Set([
          ...(session.reportContext?.appliedChunkIds ?? []),
          ...selectedChunks.map((chunk) => chunk.id)
        ])
      ],
      extractedUploadIds: session.reportContext?.extractedUploadIds ?? [],
      updatedAt: new Date().toISOString()
    });

    if (!nextSession) {
      throw new NotFoundError(`Session ${session.id} was not found.`);
    }

    if (draftChanged) {
      this.queueFettleSync?.(nextSession, "draft_updated");
    }

    return nextSession;
  }

  private async getOwnedSession(
    id: string,
    doctorId?: string
  ): Promise<ConsultationSession> {
    const sessionId = parseSessionId(id);
    const session = await this.repository.getSessionById(sessionId);
    if (!session) {
      throw new NotFoundError(`Session ${sessionId} was not found.`);
    }
    if (doctorId) {
      assertSessionOwnership(session, doctorId);
    }
    return session;
  }

  private async assertReportContextJobIsActive(
    sessionId: string,
    jobId: string
  ): Promise<void> {
    const job = await this.repository.getReportContextExtractionJob(sessionId, jobId);
    if (!job) {
      throw new NotFoundError(`Report context job ${jobId} was not found.`);
    }
    if (job.status === "canceled") {
      throw new InvalidSessionStateError("Report extraction was canceled.");
    }
  }
}

function selectReportContextChunks(
  chunks: ReportContextChunk[],
  chunkIds: string[],
  customChunks: ReportContextChunk[] = []
): ReportContextChunk[] {
  const uniqueChunkIds = [...new Set(chunkIds)];

  if (uniqueChunkIds.length === 0) {
    throw new InvalidSessionStateError("Select at least one report context item.");
  }

  const chunksById = new Map(
    [...chunks, ...customChunks].map((chunk) => [chunk.id, chunk])
  );
  const selectedChunks = uniqueChunkIds.map((chunkId) => chunksById.get(chunkId));

  if (selectedChunks.some((chunk) => !chunk)) {
    throw new NotFoundError("One or more report context items were not found.");
  }

  return selectedChunks as ReportContextChunk[];
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

async function estimateReportContextUnits(
  uploads: Array<{ mimeType: string; data: Buffer }>
): Promise<number> {
  let units = 0;
  for (const upload of uploads) {
    if (upload.mimeType !== "application/pdf") {
      units += 1;
      continue;
    }
    try {
      const pdf = await PDFDocument.load(upload.data, { ignoreEncryption: true });
      units += Math.max(1, pdf.getPages().length);
    } catch {
      units += 1;
    }
  }
  return units;
}

function estimateEtaBand(progress: {
  totalUnits: number;
  completedUnits: number;
  failedUnits: number;
  elapsedMs: number;
}): { etaSecondsLower: number; etaSecondsUpper: number } {
  const doneUnits = progress.completedUnits + progress.failedUnits;
  const remainingUnits = Math.max(0, progress.totalUnits - doneUnits);
  if (remainingUnits === 0) {
    return { etaSecondsLower: 0, etaSecondsUpper: 0 };
  }

  const safeDoneUnits = Math.max(doneUnits, 1);
  const avgPerUnitMs = progress.elapsedMs / safeDoneUnits;
  const remainingMs = avgPerUnitMs * remainingUnits;
  const lower = Math.max(2, Math.round((remainingMs * 0.7) / 1000));
  const upper = Math.max(lower + 2, Math.round((remainingMs * 1.4) / 1000));
  return {
    etaSecondsLower: lower,
    etaSecondsUpper: upper
  };
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
