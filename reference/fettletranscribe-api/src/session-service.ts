import {
  type CreateSessionRequest,
  type ConsultationSession,
  type ReportContextExtractionJob,
  type CaseStatus,
  type DoctorProfile,
  type DraftConsultationDocument,
  type ListSessionsQuery,
  type PatientHistorySession,
  type PatientProfile,
  type PrepareSessionPdfDeliveryRequest,
  type ReportContextChunk,
  type SessionPdfDeliveryStatus,
  type SessionDraft,
  draftConsultationDocumentSchema
} from "@medivoice/shared";
import { z } from "zod";

import { UnauthorizedError } from "./errors.js";
import { logError } from "./logger.js";
import { DocumentService } from "./document-service.js";
import { type PdfDeliveryService } from "./pdf-delivery-service.js";
import { type FettleSyncService } from "./fettle-sync-service.js";
import { emitKpi } from "./kpi.js";
import {
  MockReportContextAdapter,
  type ReportContextAdapter
} from "./report-context-service.js";
import { SessionArtifactsService } from "./session-artifacts-service.js";
import { SessionBasicsService } from "./session-basics-service.js";
import {
  type ReportContextUpload,
  SessionReportContextService
} from "./session-report-context-service.js";
import {
  SessionTranscriptionService,
  type SessionAudioUpload
} from "./session-transcription-service.js";
import { SessionWorkflowService } from "./session-workflow-service.js";
import { type SessionRepository } from "./session-repository.js";
import { type TranscriptionAdapter } from "./transcription-adapter.js";

export class SessionService {
  private readonly artifactsService: SessionArtifactsService;
  private readonly basicsService: SessionBasicsService;
  private readonly reportContextSessionService: SessionReportContextService;
  private readonly transcriptionService: SessionTranscriptionService;
  private readonly workflowService: SessionWorkflowService;

  constructor(
    private readonly repository: SessionRepository,
    private readonly transcriptionAdapter?: TranscriptionAdapter,
    private readonly documentService = new DocumentService(),
    private readonly pdfDeliveryService?: PdfDeliveryService,
    private readonly reportContextAdapter: ReportContextAdapter = new MockReportContextAdapter(),
    private readonly fettleSyncService?: FettleSyncService
  ) {
    this.basicsService = new SessionBasicsService(repository);
    this.artifactsService = new SessionArtifactsService(
      repository,
      documentService,
      pdfDeliveryService
    );
    this.transcriptionService = new SessionTranscriptionService(
      repository,
      transcriptionAdapter,
      (session, trigger) => this.queueFettleSync(session, trigger)
    );
    this.reportContextSessionService = new SessionReportContextService(
      repository,
      documentService,
      reportContextAdapter,
      (session, trigger) => this.queueFettleSync(session, trigger)
    );
    this.workflowService = new SessionWorkflowService(
      repository,
      documentService,
      (session, trigger) => this.queueFettleSync(session, trigger)
    );
  }

  async createSession(
    input:
      | CreateSessionRequest
      | SessionDraft,
    doctor?: DoctorProfile,
    options?: {
      id?: string;
      createdAt?: string;
    }
  ): Promise<ConsultationSession> {
    return this.basicsService.createSession(input, doctor, options);
  }

  async getPatientProfileByPhone(
    phoneNumber: string,
    doctorId?: string
  ): Promise<PatientProfile | null> {
    return this.basicsService.getPatientProfileByPhone(phoneNumber, doctorId);
  }

  async getSessionById(id: string, doctorId?: string): Promise<ConsultationSession> {
    return this.basicsService.getSessionById(id, doctorId);
  }

  async listSessions(
    query: ListSessionsQuery,
    doctorId?: string
  ): Promise<ConsultationSession[]> {
    return this.basicsService.listSessions(query, doctorId);
  }

  async listPatientHistorySessions(
    id: string,
    doctorId?: string
  ): Promise<PatientHistorySession[]> {
    return this.basicsService.listPatientHistorySessions(id, doctorId);
  }

  async uploadSessionAudio(
    id: string,
    input: SessionAudioUpload,
    doctorId?: string
  ): Promise<ConsultationSession> {
    return this.transcriptionService.uploadSessionAudio(id, input, doctorId);
  }

  async submitSessionTranscription(
    id: string,
    doctorId?: string,
    signal?: AbortSignal,
    idempotencyKey?: string
  ): Promise<ConsultationSession> {
    return this.transcriptionService.submitSessionTranscription(
      id,
      doctorId,
      signal,
      idempotencyKey
    );
  }

  async requestSessionTranscription(
    id: string,
    doctorId?: string,
    idempotencyKey?: string,
    requestId?: string
  ): Promise<ConsultationSession> {
    return this.transcriptionService.requestSessionTranscription(
      id,
      doctorId,
      idempotencyKey,
      requestId
    );
  }

  async startSessionTranscription(
    id: string,
    doctorId?: string,
    idempotencyKey?: string
  ): Promise<ConsultationSession> {
    return this.transcriptionService.startSessionTranscription(
      id,
      doctorId,
      idempotencyKey
    );
  }

  async processSessionTranscription(
    id: string,
    doctorId?: string,
    signal?: AbortSignal,
    executionMode: "interactive" | "background" = "interactive"
  ): Promise<ConsultationSession> {
    return this.transcriptionService.processSessionTranscription(
      id,
      doctorId,
      signal,
      executionMode
    );
  }

  async updateDraftDocument(
    id: string,
    input: DraftConsultationDocument,
    doctorId?: string
  ): Promise<ConsultationSession> {
    return this.workflowService.updateDraftDocument(id, input, doctorId);
  }

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
    return this.reportContextSessionService.uploadReportContextFile(id, input, doctorId);
  }

  async deleteReportContextUpload(
    id: string,
    uploadId: string,
    doctorId?: string
  ): Promise<void> {
    return this.reportContextSessionService.deleteReportContextUpload(id, uploadId, doctorId);
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
    return this.reportContextSessionService.listReportContextUploads(id, doctorId);
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
    return this.reportContextSessionService.getReportContextUploadContent(
      id,
      uploadId,
      doctorId
    );
  }

  async extractReportContext(
    id: string,
    uploadIds: string[],
    doctorId?: string,
    options?: { jobId?: string }
  ): Promise<ConsultationSession> {
    return this.reportContextSessionService.extractReportContext(
      id,
      uploadIds,
      doctorId,
      options
    );
  }

  async startReportContextExtractionJob(
    id: string,
    uploadIds: string[],
    doctorId?: string
  ): Promise<ReportContextExtractionJob> {
    return this.reportContextSessionService.startReportContextExtractionJob(
      id,
      uploadIds,
      doctorId
    );
  }

  async getReportContextExtractionJob(
    id: string,
    jobId: string,
    doctorId?: string
  ): Promise<ReportContextExtractionJob> {
    return this.reportContextSessionService.getReportContextExtractionJob(
      id,
      jobId,
      doctorId
    );
  }

  async cancelReportContextExtractionJob(
    id: string,
    jobId: string,
    doctorId?: string
  ): Promise<ReportContextExtractionJob> {
    return this.reportContextSessionService.cancelReportContextExtractionJob(
      id,
      jobId,
      doctorId
    );
  }

  async recoverStaleReportContextExtractionJobs(staleAfterMs: number): Promise<number> {
    return this.reportContextSessionService.recoverStaleReportContextExtractionJobs(
      staleAfterMs
    );
  }

  async suggestReportContextDraft(
    id: string,
    chunkIds: string[],
    customChunks?: ReportContextChunk[],
    doctorId?: string
  ): Promise<{
    draftDocument: DraftConsultationDocument;
    changedFields: string[];
  }> {
    return this.reportContextSessionService.suggestReportContextDraft(
      id,
      chunkIds,
      customChunks,
      doctorId
    );
  }

  async applyReportContextDraft(
    id: string,
    chunkIds: string[],
    customChunks: ReportContextChunk[] | undefined,
    draftDocument: DraftConsultationDocument,
    doctorId?: string
  ): Promise<ConsultationSession> {
    return this.reportContextSessionService.applyReportContextDraft(
      id,
      chunkIds,
      customChunks,
      draftConsultationDocumentSchema.parse(draftDocument),
      doctorId
    );
  }

  async updateCaseStatus(
    id: string,
    caseStatus: CaseStatus,
    doctorId?: string
  ): Promise<ConsultationSession> {
    return this.workflowService.updateCaseStatus(id, caseStatus, doctorId);
  }

  async generateSessionPdf(id: string, doctorId?: string): Promise<ConsultationSession> {
    return this.artifactsService.generateSessionPdf(id, doctorId);
  }

  async prepareSessionPdfDelivery(
    id: string,
    input: PrepareSessionPdfDeliveryRequest,
    doctorId?: string
  ): Promise<SessionPdfDeliveryStatus> {
    return this.artifactsService.prepareSessionPdfDelivery(id, input, doctorId);
  }

  async getSessionPdfDeliveryStatus(
    id: string,
    doctorId?: string
  ): Promise<SessionPdfDeliveryStatus> {
    return this.artifactsService.getSessionPdfDeliveryStatus(id, doctorId);
  }

  async sendSessionPdfDelivery(
    id: string,
    input: PrepareSessionPdfDeliveryRequest,
    doctorId?: string
  ): Promise<SessionPdfDeliveryStatus> {
    return this.artifactsService.sendSessionPdfDelivery(id, input, doctorId);
  }

  async getTranscriptDownload(
    id: string,
    doctorId?: string
  ): Promise<{ fileName: string; mimeType: string; data: Buffer }> {
    return this.artifactsService.getTranscriptDownload(id, doctorId);
  }

  async getAudioDownload(
    id: string,
    doctorId?: string
  ): Promise<{ fileName: string; mimeType: string; data: Buffer }> {
    return this.artifactsService.getAudioDownload(id, doctorId);
  }

  async getPdfDownload(
    id: string,
    doctorId?: string
  ): Promise<{ fileName: string; mimeType: string; data: Buffer }> {
    return this.artifactsService.getPdfDownload(id, doctorId);
  }

  private queueFettleSync(
    session: ConsultationSession,
    trigger: "transcription_completed" | "draft_updated"
  ): void {
    if (!this.fettleSyncService?.enabled) {
      return;
    }
    void this.fettleSyncService.enqueueSessionSync(session, trigger).catch((error) => {
      logError(
        "fettle_sync_enqueue_unhandled_failure",
        { sessionId: session.id, trigger },
        { scope: "fettle_sync", error }
      );
    });
  }

}
