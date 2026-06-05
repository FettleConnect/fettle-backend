import { createHmac, timingSafeEqual } from "node:crypto";

import {
  type ConsultationSession,
  type PdfDeliveryJob,
  type PrepareSessionPdfDeliveryRequest,
  type SessionPdfDeliverySettings,
  type SessionPdfDeliveryStatus,
  prepareSessionPdfDeliveryRequestSchema,
  sessionPdfDeliveryStatusSchema
} from "@medivoice/shared";

import { type PdfAssetStorage } from "./pdf-asset-storage.js";
import {
  type PdfDeliveryJobDraft,
  type PdfDeliveryRepository
} from "./pdf-delivery-repository.js";
import { type SessionRepository } from "./session-repository.js";
import {
  mapTwilioMessageStatus,
  type WhatsappDeliverySender
} from "./whatsapp-delivery.js";
import { logDebug, logError, logInfo, logWarn } from "./logger.js";
import { emitKpi } from "./kpi.js";
import { runWithReliability } from "./reliability.js";

export interface PdfDeliverySendResult {
  provider: string;
  providerMessageId?: string;
}

const QUEUED_BLOCK_TTL_MS = 2 * 60 * 1000;
const ACTIVE_JOB_BLOCK_TTL_MS = 2 * 60 * 1000;
let consecutiveEmptyClaimBatches = 0;

export interface PublicPdfDeliveryAsset {
  sessionId: string;
  fileName: string;
  mimeType: string;
  data: Buffer;
}

export class MockPdfDeliverySender implements WhatsappDeliverySender {
  async send(context: { job: PdfDeliveryJob }): Promise<PdfDeliverySendResult> {
    return {
      provider: "mock-whatsapp",
      providerMessageId: `mock-${context.job.id}`
    };
  }
}

export class PdfDeliveryService {
  constructor(
    private readonly repository: PdfDeliveryRepository,
    private readonly sessionRepository: SessionRepository,
    private readonly assetStorage?: PdfAssetStorage,
    private readonly sender: WhatsappDeliverySender = new MockPdfDeliverySender(),
    private readonly publicMedia?:
      | {
          baseUrl: string;
          signingSecret: string;
          tokenTtlSeconds?: number;
        }
      | undefined
  ) {}

  async prepareSessionDelivery(
    session: ConsultationSession,
    input: PrepareSessionPdfDeliveryRequest
  ): Promise<SessionPdfDeliveryStatus> {
    const parsed = prepareSessionPdfDeliveryRequestSchema.parse(input);
    const settings = await this.repository.saveSettings(session.id, {
      patientEnabled: parsed.patientEnabled,
      pharmacyEnabled: parsed.pharmacyEnabled,
      ...(parsed.pharmacy ? { pharmacy: parsed.pharmacy } : {}),
      updatedAt: new Date().toISOString()
    });

    return this.buildStatus(session.id, settings);
  }

  async getSessionDeliveryStatus(
    sessionId: string
  ): Promise<SessionPdfDeliveryStatus> {
    const settings = await this.resolveSettings(sessionId);
    return this.buildStatus(sessionId, settings);
  }

  async listJobsForAdmin(input?: {
    sessionId?: string;
    status?: PdfDeliveryJob["status"];
    recipientType?: PdfDeliveryJob["recipientType"];
  }): Promise<PdfDeliveryJob[]> {
    return this.repository.listJobsForAdmin(input);
  }

  async getJobByIdForAdmin(jobId: string): Promise<PdfDeliveryJob | null> {
    return this.repository.getJobById(jobId);
  }

  async enqueueForSession(session: ConsultationSession): Promise<PdfDeliveryJob[]> {
    const settings = await this.resolveSettings(session.id);
    const existingJobs = await this.repository.listJobsBySession(session.id);
    const drafts = buildJobDrafts(session, settings).filter(
      (draft) =>
        !existingJobs.some(
          (job) =>
            job.recipientType === draft.recipientType &&
            isBlockingJobForEnqueue(job)
        )
    );

    if (!drafts.length) {
      return [];
    }

    return this.repository.createJobs(drafts);
  }

  async processPendingJobs(limit = 10): Promise<PdfDeliveryJob[]> {
    const claimed = await this.repository.claimPendingJobs(limit);
    const completed: PdfDeliveryJob[] = [];
    if (claimed.length === 0) {
      consecutiveEmptyClaimBatches += 1;
      if (consecutiveEmptyClaimBatches % 60 === 0) {
        logDebug(
          "pdf_delivery_batch_idle",
          {
            emptyBatchStreak: consecutiveEmptyClaimBatches,
            requestedLimit: limit
          },
          { scope: "delivery" }
        );
      }
    } else {
      consecutiveEmptyClaimBatches = 0;
      logInfo(
        "pdf_delivery_batch_claimed",
        { claimedCount: claimed.length, requestedLimit: limit },
        { scope: "delivery" }
      );
    }

    for (const job of claimed) {
      const jobStartedAt = Date.now();
      try {
        logInfo(
          "pdf_delivery_job_processing_started",
          {
            jobId: job.id,
            sessionId: job.sessionId,
            recipientType: job.recipientType,
            attemptCount: job.attemptCount
          },
          { scope: "delivery" }
        );
        const session = await this.sessionRepository.getSessionById(job.sessionId);
        const generatedDocument = await this.sessionRepository.getGeneratedDocument(
          job.sessionId
        );

        if (!session || !generatedDocument) {
          throw new Error("The consultation PDF could not be loaded for delivery.");
        }

        logInfo(
          "pdf_delivery_asset_upload_started",
          {
            jobId: job.id,
            sessionId: job.sessionId,
            hasAssetStorage: Boolean(this.assetStorage)
          },
          { scope: "delivery" }
        );
        const mediaAsset = this.assetStorage
          ? (
              await runWithReliability(
                () => this.assetStorage!.storePdf({
                sessionId: session.id,
                fileName: generatedDocument.artifact.fileName,
                mimeType: generatedDocument.artifact.mimeType,
                data: generatedDocument.pdf
                }),
                {
                  operation: "pdf_delivery:s3_store_pdf",
                  timeoutMs: 20_000,
                  maxRetries: 2,
                  retryDelayMs: 500
                }
              )
            )
          : null;
        const mediaUrl = this.publicMedia
          ? this.createPublicMediaUrl(
              job.sessionId,
              generatedDocument.artifact.fileName
            )
          : (mediaAsset?.url ?? "");
        logInfo(
          "pdf_delivery_asset_upload_completed",
          {
            jobId: job.id,
            sessionId: job.sessionId,
            hasMediaUrl: mediaUrl.length > 0
          },
          { scope: "delivery" }
        );
        logInfo(
          "pdf_delivery_provider_send_started",
          {
            jobId: job.id,
            sessionId: job.sessionId,
            recipientType: job.recipientType
          },
          { scope: "delivery" }
        );
        const result = await runWithReliability(
          () =>
            this.sender.send({
              job,
              session,
              generatedDocument,
              mediaUrl
            }),
          {
            operation: "pdf_delivery:whatsapp_send",
            timeoutMs: 20_000,
            maxRetries: 2,
            retryDelayMs: 1_000
          }
        );
        logInfo(
          "pdf_delivery_provider_send_completed",
          {
            jobId: job.id,
            sessionId: job.sessionId,
            provider: result.provider,
            providerMessageId: result.providerMessageId
          },
          { scope: "delivery" }
        );
        const updated = await this.repository.updateJobStatus(job.id, "queued", {
          provider: result.provider,
          ...(result.providerMessageId
            ? { providerMessageId: result.providerMessageId }
            : {}),
          ...(result.providerMessageId
            ? { sentAt: new Date().toISOString() }
            : {})
        });

        completed.push(updated ?? job);
        emitKpi(
          "pdf_delivery.job_process",
          "success",
          {
            jobId: job.id,
            sessionId: job.sessionId,
            recipientType: job.recipientType,
            elapsedMs: Date.now() - jobStartedAt
          },
          { scope: "delivery" }
        );
        logInfo(
          "pdf_delivery_job_status_updated",
          {
            jobId: job.id,
            sessionId: job.sessionId,
            nextStatus: "queued",
            provider: result.provider,
            providerMessageId: result.providerMessageId
          },
          { scope: "delivery" }
        );
      } catch (error) {
        const message =
          error instanceof Error
            ? error.message
            : "PDF delivery failed unexpectedly.";
        const failed = await this.repository.updateJobStatus(job.id, "failed", {
          lastError: message
        });

        completed.push(failed ?? job);
        emitKpi(
          "pdf_delivery.job_process",
          "failure",
          {
            jobId: job.id,
            sessionId: job.sessionId,
            recipientType: job.recipientType,
            elapsedMs: Date.now() - jobStartedAt
          },
          { scope: "delivery" }
        );
        logError(
          "pdf_delivery_job_failed",
          {
            jobId: job.id,
            sessionId: job.sessionId,
            recipientType: job.recipientType,
            lastError: message
          },
          {
            scope: "delivery",
            error
          }
        );
      }
    }

    return completed;
  }

  async handleProviderStatusUpdate(input: {
    providerMessageId: string;
    providerStatus?: string;
    errorMessage?: string;
  }): Promise<PdfDeliveryJob | null> {
    logInfo(
      "pdf_delivery_provider_status_received",
      {
        providerMessageId: input.providerMessageId,
        providerStatus: input.providerStatus,
        hasErrorMessage: Boolean(input.errorMessage?.trim())
      },
      { scope: "delivery" }
    );
    const job = await this.repository.getJobByProviderMessageId(
      input.providerMessageId
    );

    if (!job) {
      logWarn(
        "pdf_delivery_provider_status_job_not_found",
        { providerMessageId: input.providerMessageId },
        { scope: "delivery" }
      );
      return null;
    }

    const nextStatus = mapTwilioMessageStatus(input.providerStatus);

    if (!shouldApplyProviderStatusTransition(job.status, nextStatus)) {
      logWarn(
        "pdf_delivery_provider_status_ignored_out_of_order",
        {
          jobId: job.id,
          sessionId: job.sessionId,
          providerMessageId: input.providerMessageId,
          currentStatus: job.status,
          nextStatus
        },
        { scope: "delivery" }
      );
      return job;
    }

    const updatedJob = await this.repository.updateJobStatus(job.id, nextStatus, {
      ...(input.errorMessage?.trim()
        ? { lastError: input.errorMessage.trim() }
        : {}),
      ...(nextStatus !== "failed"
        ? { sentAt: job.sentAt ?? new Date().toISOString() }
        : {})
    });

    logInfo(
      "pdf_delivery_provider_status_applied",
      {
        jobId: job.id,
        sessionId: job.sessionId,
        providerMessageId: input.providerMessageId,
        nextStatus,
        hasErrorMessage: Boolean(input.errorMessage?.trim())
      },
      { scope: "delivery" }
    );

    return updatedJob;
  }

  async getPublicMediaAsset(
    token: string
  ): Promise<PublicPdfDeliveryAsset | null> {
    const sessionId = this.resolveSessionIdFromMediaToken(token);

    if (!sessionId) {
      return null;
    }

    const generatedDocument = await this.sessionRepository.getGeneratedDocument(
      sessionId
    );

    if (!generatedDocument) {
      return null;
    }

    return {
      sessionId,
      fileName: generatedDocument.artifact.fileName,
      mimeType: generatedDocument.artifact.mimeType,
      data: generatedDocument.pdf
    };
  }

  private async resolveSettings(
    sessionId: string
  ): Promise<SessionPdfDeliverySettings> {
    return (await this.repository.getSettings(sessionId)) ?? defaultSettings();
  }

  private async buildStatus(
    sessionId: string,
    settings: SessionPdfDeliverySettings
  ): Promise<SessionPdfDeliveryStatus> {
    const jobs = await this.repository.listJobsBySession(sessionId);

    return sessionPdfDeliveryStatusSchema.parse({
      settings,
      jobs
    });
  }

  private createPublicMediaUrl(sessionId: string, fileName: string): string {
    if (!this.publicMedia) {
      return "";
    }

    const baseUrl = this.publicMedia.baseUrl.replace(/\/$/, "");
    const token = this.createMediaToken(sessionId);
    const publicFileName = sanitizePublicMediaFileName(fileName);
    return `${baseUrl}/public/pdf-delivery/media/${encodeURIComponent(publicFileName)}?token=${encodeURIComponent(token)}`;
  }

  private createMediaToken(sessionId: string): string {
    if (!this.publicMedia) {
      return "";
    }

    const tokenTtlSeconds = this.publicMedia.tokenTtlSeconds ?? 900;
    const payload = JSON.stringify({
      sessionId,
      exp: Math.floor(Date.now() / 1000) + tokenTtlSeconds
    });
    const encodedPayload = Buffer.from(payload, "utf8").toString("base64url");
    const signature = createHmac("sha256", this.publicMedia.signingSecret)
      .update(encodedPayload)
      .digest("base64url");

    return `${encodedPayload}.${signature}`;
  }

  private resolveSessionIdFromMediaToken(token: string): string | null {
    if (!this.publicMedia) {
      return null;
    }

    const [encodedPayload, receivedSignature] = token.split(".");

    if (!encodedPayload || !receivedSignature) {
      return null;
    }

    const expectedSignature = createHmac("sha256", this.publicMedia.signingSecret)
      .update(encodedPayload)
      .digest("base64url");

    if (
      expectedSignature.length !== receivedSignature.length ||
      !timingSafeEqual(
        Buffer.from(expectedSignature, "utf8"),
        Buffer.from(receivedSignature, "utf8")
      )
    ) {
      return null;
    }

    const parsedPayload = safeParseMediaTokenPayload(encodedPayload);

    if (!parsedPayload) {
      return null;
    }

    const nowEpochSeconds = Math.floor(Date.now() / 1000);
    if (parsedPayload.exp <= nowEpochSeconds) {
      return null;
    }

    return parsedPayload.sessionId;
  }
}

export class PdfDeliveryWorker {
  constructor(private readonly deliveryService: PdfDeliveryService) {}

  async processNextBatch(limit = 10): Promise<PdfDeliveryJob[]> {
    return this.deliveryService.processPendingJobs(limit);
  }
}

function defaultSettings(): SessionPdfDeliverySettings {
  const updatedAt = new Date().toISOString();

  return {
    patientEnabled: true,
    pharmacyEnabled: false,
    updatedAt
  };
}

function buildJobDrafts(
  session: ConsultationSession,
  settings: SessionPdfDeliverySettings
): PdfDeliveryJobDraft[] {
  const drafts: PdfDeliveryJobDraft[] = [];

  if (settings.patientEnabled) {
    drafts.push({
      sessionId: session.id,
      recipientType: "patient",
      recipientName: session.patient.name,
      phoneNumber: session.patient.phoneNumber
    });
  }

  if (settings.pharmacyEnabled && settings.pharmacy) {
    drafts.push({
      sessionId: session.id,
      recipientType: "pharmacy",
      recipientName: settings.pharmacy.name,
      phoneNumber: settings.pharmacy.phoneNumber
    });
  }

  return drafts;
}

function safeParseMediaTokenPayload(
  encodedPayload: string
): { sessionId: string; exp: number } | null {
  try {
    const payload = JSON.parse(
      Buffer.from(encodedPayload, "base64url").toString("utf8")
    ) as { sessionId?: unknown; exp?: unknown };

    if (
      typeof payload.sessionId !== "string" ||
      payload.sessionId.trim().length === 0 ||
      typeof payload.exp !== "number" ||
      !Number.isFinite(payload.exp)
    ) {
      return null;
    }

    return {
      sessionId: payload.sessionId.trim(),
      exp: payload.exp
    };
  } catch {
    return null;
  }
}

function sanitizePublicMediaFileName(fileName: string): string {
  const trimmed = fileName.trim();

  if (!trimmed) {
    return "consultation-summary.pdf";
  }

  const normalized = trimmed.replace(/[^a-zA-Z0-9._-]+/g, "-");

  if (normalized.toLowerCase().endsWith(".pdf")) {
    return normalized;
  }

  return `${normalized}.pdf`;
}

function isBlockingJobForEnqueue(job: PdfDeliveryJob): boolean {
  if (job.status === "queued") {
    const updatedAtEpochMs = Date.parse(job.updatedAt);

    if (!Number.isNaN(updatedAtEpochMs)) {
      const queuedAgeMs = Date.now() - updatedAtEpochMs;

      if (queuedAgeMs > QUEUED_BLOCK_TTL_MS) {
        logWarn(
          "pdf_delivery_queued_job_not_blocking_retry",
          { jobId: job.id, queuedAgeMs, thresholdMs: QUEUED_BLOCK_TTL_MS },
          { scope: "delivery" }
        );
        return false;
      }
    }

    return true;
  }

  if (job.status === "pending" || job.status === "processing") {
    const updatedAtEpochMs = Date.parse(job.updatedAt);

    if (!Number.isNaN(updatedAtEpochMs)) {
      const activeAgeMs = Date.now() - updatedAtEpochMs;

      if (activeAgeMs > ACTIVE_JOB_BLOCK_TTL_MS) {
        logWarn(
          "pdf_delivery_active_job_not_blocking_retry",
          { jobId: job.id, activeAgeMs, thresholdMs: ACTIVE_JOB_BLOCK_TTL_MS },
          { scope: "delivery" }
        );
        return false;
      }
    }

    return true;
  }

  return false;
}

function shouldApplyProviderStatusTransition(
  currentStatus: PdfDeliveryJob["status"],
  nextStatus: PdfDeliveryJob["status"]
): boolean {
  if (currentStatus === nextStatus) {
    return false;
  }

  if (currentStatus === "failed" || currentStatus === "read") {
    return false;
  }

  if (currentStatus === "delivered") {
    return nextStatus === "read";
  }

  if (currentStatus === "sent") {
    return ["delivered", "read", "failed"].includes(nextStatus);
  }

  if (currentStatus === "queued") {
    return ["sent", "delivered", "read", "failed"].includes(nextStatus);
  }

  return true;
}
