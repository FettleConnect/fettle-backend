import {
  Binary,
  MongoClient,
  MongoServerError,
  type ClientSession,
  type Collection
} from "mongodb";

import {
  type ConsultationSession,
  type ConsultationMetadata,
  type CaseStatus,
  type DocumentArtifact,
  type DraftDocumentProvenance,
  type DraftConsultationDocument,
  type SessionReportContext,
  type ReportContextExtractionJob,
  type SessionAudioArtifact,
  type SessionDraft,
  type TranscriptionStage,
  type SessionTranscription,
  type TranscriptSegment,
  type PatientProfile,
  consultationSessionSchema,
  sessionReportContextSchema,
  reportContextExtractionJobSchema,
  sessionTranscriptionSchema
} from "@medivoice/shared";
import { createSessionAggregate, matchesSessionSearch } from "@medivoice/domain";
import { type GeneratedDocumentArtifact } from "./document-service.js";
import { type StoredSessionAudio } from "./transcription-adapter.js";

export interface SessionRepository {
  createSession(
    draft: SessionDraft,
    options?: {
      id?: string;
      createdAt?: string;
    }
  ): Promise<ConsultationSession>;
  getSessionById(id: string): Promise<ConsultationSession | null>;
  listSessions(
    search: string | undefined,
    doctorId?: string
  ): Promise<ConsultationSession[]>;
  listPatientHistorySessions(input: {
    patientPhoneNumber: string;
    currentSessionId: string;
    currentSessionCreatedAt: string;
    doctorId?: string;
    limit?: number;
  }): Promise<ConsultationSession[]>;
  findMostRecentPatientForDoctorPhone(input: {
    doctorId: string;
    phoneNumber: string;
  }): Promise<{ phoneNumber: string; name: string; age?: number } | null>;
  getPatientProfileByPhone(phoneNumber: string): Promise<PatientProfile | null>;
  upsertPatientProfile(input: PatientProfile): Promise<void>;
  uploadSessionAudio(
    id: string,
    draft: SessionAudioUploadDraft
  ): Promise<ConsultationSession | null>;
  getSessionAudio(id: string): Promise<StoredSessionAudio | null>;
  markTranscriptionProcessing(
    id: string,
    provider: string,
    requestedAt: string,
    maxAttempts?: number,
    requestKey?: string
  ): Promise<ConsultationSession | null>;
  completeTranscription(
    id: string,
    draft: SessionTranscriptionCompletionDraft,
    expectedRequestKey?: string
  ): Promise<ConsultationSession | null>;
  failTranscription(
    id: string,
    draft: SessionTranscriptionFailureDraft,
    expectedRequestKey?: string
  ): Promise<ConsultationSession | null>;
  setTranscriptionStage(
    id: string,
    stage: TranscriptionStage,
    expectedRequestKey?: string
  ): Promise<ConsultationSession | null>;
  recoverInterruptedTranscriptions(): Promise<number>;
  recoverStaleProcessingTranscriptions(staleAfterMs: number): Promise<number>;
  cleanupLegacyNullTranscriptionFields(): Promise<number>;
  updateDraftDocument(
    id: string,
    draftDocument: DraftConsultationDocument,
    draftProvenance?: DraftDocumentProvenance
  ): Promise<ConsultationSession | null>;
  storeReportContextUpload(
    id: string,
    upload: ReportContextUploadDraft
  ): Promise<StoredReportContextUpload>;
  deleteReportContextUpload(sessionId: string, uploadId: string): Promise<void>;
  getReportContextUploads(
    sessionId: string,
    uploadIds?: string[]
  ): Promise<StoredReportContextUpload[]>;
  updateReportContext(
    id: string,
    reportContext: SessionReportContext
  ): Promise<ConsultationSession | null>;
  updateCaseStatus(
    id: string,
    caseStatus: CaseStatus
  ): Promise<ConsultationSession | null>;
  closePatientCaseThroughSession(input: {
    patientPhoneNumber: string;
    doctorId: string;
    throughCreatedAt: string;
    updatedAt: string;
  }): Promise<void>;
  storeGeneratedDocument(
    id: string,
    generated: GeneratedDocumentArtifact
  ): Promise<ConsultationSession | null>;
  getGeneratedDocument(
    id: string
  ): Promise<(GeneratedDocumentArtifact & { artifact: DocumentArtifact }) | null>;
  createReportContextExtractionJob(input: {
    sessionId: string;
    uploadIds: string[];
  }): Promise<ReportContextExtractionJob>;
  getReportContextExtractionJob(
    sessionId: string,
    jobId: string
  ): Promise<ReportContextExtractionJob | null>;
  updateReportContextExtractionJob(
    sessionId: string,
    jobId: string,
    updates: Partial<ReportContextExtractionJob>
  ): Promise<ReportContextExtractionJob | null>;
  findActiveReportContextExtractionJob(
    sessionId: string
  ): Promise<ReportContextExtractionJob | null>;
  recoverStaleReportContextExtractionJobs(staleAfterMs: number): Promise<number>;
}

const maxListedSessions = 200;

export interface SessionAudioUploadDraft {
  fileName: string;
  mimeType: string;
  sizeBytes: number;
  durationSeconds: number;
  data: Buffer;
}

export interface SessionTranscriptionCompletionDraft {
  provider: string;
  requiresRecovery: boolean;
  summary: string;
  transcriptSegments: TranscriptSegment[];
  metadata: ConsultationMetadata;
  draftDocument: DraftConsultationDocument;
  draftProvenance?: DraftDocumentProvenance;
}

export interface SessionTranscriptionFailureDraft {
  provider: string;
  error: string;
  errorClass?: string;
  retryable?: boolean;
  exhaustedRetryBudget?: boolean;
}

export interface ReportContextUploadDraft {
  fileName: string;
  mimeType: string;
  sizeBytes: number;
  data: Buffer;
  expiresAt: string;
}

export interface StoredReportContextUpload extends ReportContextUploadDraft {
  id: string;
  sessionId: string;
  uploadedAt: string;
}

type StoredReportContextExtractionJob = ReportContextExtractionJob;

export class InMemorySessionRepository implements SessionRepository {
  private readonly sessions = new Map<string, ConsultationSession>();
  private readonly audioArtifacts = new Map<
    string,
    SessionAudioUploadDraft & { metadata: SessionAudioArtifact }
  >();
  private readonly generatedDocuments = new Map<
    string,
    GeneratedDocumentArtifact & { sessionId: string }
  >();
  private readonly reportUploads = new Map<string, StoredReportContextUpload>();
  private readonly reportExtractionJobs = new Map<
    string,
    StoredReportContextExtractionJob
  >();
  private readonly patientProfiles = new Map<string, PatientProfile>();

  async createSession(
    draft: SessionDraft,
    options?: {
      id?: string;
      createdAt?: string;
    }
  ): Promise<ConsultationSession> {
    const session = createSessionAggregate(draft, options);
    this.sessions.set(session.id, session);
    return session;
  }

  async getSessionById(id: string): Promise<ConsultationSession | null> {
    return this.sessions.get(id) ?? null;
  }

  async listSessions(
    search: string | undefined,
    doctorId?: string
  ): Promise<ConsultationSession[]> {
    return [...this.sessions.values()]
      .filter((session) => (doctorId ? session.doctorId === doctorId : true))
      .filter((session) => matchesSessionSearch(session, search))
      .sort((left, right) => right.createdAt.localeCompare(left.createdAt))
      .slice(0, maxListedSessions);
  }

  async listPatientHistorySessions(input: {
    patientPhoneNumber: string;
    currentSessionId: string;
    currentSessionCreatedAt: string;
    doctorId?: string;
    limit?: number;
  }): Promise<ConsultationSession[]> {
    const limit = input.limit ?? 10;
    const normalizedTarget = normalizePhoneLookupKey(input.patientPhoneNumber);

    return [...this.sessions.values()]
      .filter((session) => session.id !== input.currentSessionId)
      .filter(
        (session) =>
          normalizePhoneLookupKey(session.patient.phoneNumber) === normalizedTarget
      )
      .filter((session) => session.createdAt < input.currentSessionCreatedAt)
      .filter((session) => (input.doctorId ? session.doctorId === input.doctorId : true))
      .sort((left, right) => right.createdAt.localeCompare(left.createdAt))
      .slice(0, limit);
  }

  async getPatientProfileByPhone(phoneNumber: string): Promise<PatientProfile | null> {
    const exact = this.patientProfiles.get(phoneNumber);
    if (exact) {
      return exact;
    }
    const normalizedTarget = normalizePhoneLookupKey(phoneNumber);
    if (!normalizedTarget) {
      return null;
    }
    for (const profile of this.patientProfiles.values()) {
      if (normalizePhoneLookupKey(profile.phoneNumber) === normalizedTarget) {
        return profile;
      }
    }
    return null;
  }

  async findMostRecentPatientForDoctorPhone(input: {
    doctorId: string;
    phoneNumber: string;
  }): Promise<{ phoneNumber: string; name: string; age?: number } | null> {
    const normalizedTarget = normalizePhoneLookupKey(input.phoneNumber);
    if (!normalizedTarget) {
      return null;
    }

    const match = [...this.sessions.values()]
      .filter((session) => session.doctorId === input.doctorId)
      .filter(
        (session) =>
          normalizePhoneLookupKey(session.patient.phoneNumber) === normalizedTarget
      )
      .sort((left, right) => right.createdAt.localeCompare(left.createdAt))[0];

    if (!match) {
      return null;
    }

    return {
      phoneNumber: match.patient.phoneNumber,
      name: match.patient.name,
      ...(typeof match.patient.age === "number" ? { age: match.patient.age } : {})
    };
  }

  async upsertPatientProfile(input: PatientProfile): Promise<void> {
    this.patientProfiles.set(input.phoneNumber, {
      phoneNumber: input.phoneNumber,
      name: input.name,
      ...(typeof input.age === "number" ? { age: input.age } : {})
    });
  }

  async uploadSessionAudio(
    id: string,
    draft: SessionAudioUploadDraft
  ): Promise<ConsultationSession | null> {
    const existingSession = this.sessions.get(id);

    if (!existingSession) {
      return null;
    }

    const uploadedAt = new Date().toISOString();
    const artifactId = existingSession.audioArtifact?.id ?? id;
    const audioArtifact: SessionAudioArtifact = {
      id: artifactId,
      fileName: draft.fileName,
      mimeType: draft.mimeType,
      sizeBytes: draft.sizeBytes,
      durationSeconds: draft.durationSeconds,
      uploadedAt
    };
    const nextSession: ConsultationSession = {
      ...existingSession,
      audioArtifact,
      transcription: {
        status: "idle",
        transcriptSegments: []
      },
      updatedAt: uploadedAt
    };

    this.audioArtifacts.set(id, {
      ...draft,
      metadata: audioArtifact
    });
    this.sessions.set(id, nextSession);

    return nextSession;
  }

  async getSessionAudio(id: string): Promise<StoredSessionAudio | null> {
    const session = this.sessions.get(id);

    if (!session?.audioArtifact) {
      return null;
    }

    const stored = this.audioArtifacts.get(session.audioArtifact.id);

    if (!stored) {
      return null;
    }

    return {
      fileName: stored.fileName,
      mimeType: stored.mimeType,
      durationSeconds: stored.durationSeconds,
      data: stored.data
    };
  }

  async markTranscriptionProcessing(
    id: string,
    provider: string,
    requestedAt: string,
    maxAttempts?: number,
    requestKey?: string
  ): Promise<ConsultationSession | null> {
    const session = this.sessions.get(id);

    if (!session) {
      return null;
    }

    if (session.transcription?.status === "processing") {
      return session;
    }

    const transcription: SessionTranscription = {
      status: "processing",
      stage: "preparing_audio",
      provider,
      requestedAt,
      ...(requestKey ? { requestKey } : {}),
      attemptCount: (session.transcription?.attemptCount ?? 0) + 1,
      maxAttempts: maxAttempts ?? session.transcription?.maxAttempts,
      transcriptSegments: session.transcription?.transcriptSegments ?? []
    };
    const nextSession: ConsultationSession = {
      ...session,
      transcription,
      updatedAt: requestedAt
    };

    this.sessions.set(id, nextSession);
    return nextSession;
  }

  async completeTranscription(
    id: string,
    draft: SessionTranscriptionCompletionDraft,
    expectedRequestKey?: string
  ): Promise<ConsultationSession | null> {
    const session = this.sessions.get(id);

    if (!session) {
      return null;
    }
    if (session.transcription?.status !== "processing") {
      return null;
    }
    if (
      expectedRequestKey &&
      session.transcription.requestKey !== expectedRequestKey
    ) {
      return null;
    }

    const updatedAt = new Date().toISOString();
    const transcription: SessionTranscription = {
      status: "completed",
      provider: draft.provider,
      attemptCount: session.transcription?.attemptCount ?? 1,
      maxAttempts: session.transcription?.maxAttempts,
      requestedAt: session.transcription?.requestedAt ?? updatedAt,
      completedAt: updatedAt,
      stage: undefined,
      requiresRecovery: draft.requiresRecovery,
      transcriptSegments: draft.transcriptSegments,
      summary: draft.summary,
      metadata: draft.metadata,
      draftDocument: draft.draftDocument,
      draftProvenance: draft.draftProvenance
    };
    const nextSession: ConsultationSession = {
      ...session,
      transcription,
      updatedAt
    };

    this.sessions.set(id, nextSession);
    return nextSession;
  }

  async failTranscription(
    id: string,
    draft: SessionTranscriptionFailureDraft,
    expectedRequestKey?: string
  ): Promise<ConsultationSession | null> {
    const session = this.sessions.get(id);

    if (!session) {
      return null;
    }
    if (session.transcription?.status !== "processing") {
      return null;
    }
    if (
      expectedRequestKey &&
      session.transcription.requestKey !== expectedRequestKey
    ) {
      return null;
    }

    const updatedAt = new Date().toISOString();
    const transcription: SessionTranscription = {
      status: "failed",
      provider: draft.provider,
      attemptCount: session.transcription?.attemptCount ?? 1,
      maxAttempts: session.transcription?.maxAttempts,
      requestedAt: session.transcription?.requestedAt ?? updatedAt,
      failedAt: updatedAt,
      stage: undefined,
      error: draft.error,
      ...(draft.errorClass ? { errorClass: draft.errorClass } : {}),
      ...(typeof draft.retryable === "boolean"
        ? { retryable: draft.retryable }
        : {}),
      ...(typeof draft.exhaustedRetryBudget === "boolean"
        ? { exhaustedRetryBudget: draft.exhaustedRetryBudget }
        : {}),
      transcriptSegments: session.transcription?.transcriptSegments ?? [],
      summary: session.transcription?.summary,
      metadata: session.transcription?.metadata,
      draftDocument: session.transcription?.draftDocument
    };
    const nextSession: ConsultationSession = {
      ...session,
      transcription,
      updatedAt
    };

    this.sessions.set(id, nextSession);
    return nextSession;
  }

  async setTranscriptionStage(
    id: string,
    stage: TranscriptionStage,
    expectedRequestKey?: string
  ): Promise<ConsultationSession | null> {
    const session = this.sessions.get(id);
    if (!session || session.transcription?.status !== "processing") {
      return null;
    }
    if (
      expectedRequestKey &&
      session.transcription.requestKey !== expectedRequestKey
    ) {
      return null;
    }

    const updatedAt = new Date().toISOString();
    const nextSession: ConsultationSession = {
      ...session,
      transcription: {
        ...session.transcription,
        stage
      },
      updatedAt
    };
    this.sessions.set(id, nextSession);
    return nextSession;
  }

  async recoverInterruptedTranscriptions(): Promise<number> {
    let recoveredCount = 0;

    for (const [id, session] of this.sessions.entries()) {
      if (session.transcription?.status !== "processing") {
        continue;
      }

      const failedAt = new Date().toISOString();
      const nextSession: ConsultationSession = {
        ...session,
        transcription: {
          ...session.transcription,
          status: "failed",
          failedAt,
          error: "Transcription stopped when the server restarted. Please retry."
        },
        updatedAt: failedAt
      };

      this.sessions.set(id, nextSession);
      recoveredCount += 1;
    }

    return recoveredCount;
  }

  async recoverStaleProcessingTranscriptions(staleAfterMs: number): Promise<number> {
    const now = Date.now();
    let recoveredCount = 0;

    for (const [id, session] of this.sessions.entries()) {
      if (session.transcription?.status !== "processing") {
        continue;
      }
      const requestedAtMs = Date.parse(session.transcription.requestedAt ?? "");
      if (!Number.isFinite(requestedAtMs)) {
        continue;
      }
      if (now - requestedAtMs < staleAfterMs) {
        continue;
      }

      const failedAt = new Date().toISOString();
      const nextSession: ConsultationSession = {
        ...session,
        transcription: {
          ...session.transcription,
          status: "failed",
          failedAt,
          error: "Transcription timed out while processing. Please retry."
        },
        updatedAt: failedAt
      };
      this.sessions.set(id, nextSession);
      recoveredCount += 1;
    }

    return recoveredCount;
  }

  async cleanupLegacyNullTranscriptionFields(): Promise<number> {
    let cleanedCount = 0;

    for (const [id, session] of this.sessions.entries()) {
      const transcription = session.transcription;

      if (!transcription) {
        continue;
      }

      const nextTranscriptionRecord = Object.fromEntries(
        Object.entries(transcription).filter(([, value]) => value !== null)
      ) as Record<string, unknown>;

      if (
        Object.keys(nextTranscriptionRecord).length ===
        Object.keys(transcription as Record<string, unknown>).length
      ) {
        continue;
      }

      this.sessions.set(id, {
        ...session,
        transcription: nextTranscriptionRecord as ConsultationSession["transcription"]
      });
      cleanedCount += 1;
    }

    return cleanedCount;
  }

  async updateDraftDocument(
    id: string,
    draftDocument: DraftConsultationDocument,
    draftProvenance?: DraftDocumentProvenance
  ): Promise<ConsultationSession | null> {
    const session = this.sessions.get(id);

    if (!session || !session.transcription) {
      return null;
    }

    const updatedAt = new Date().toISOString();
    const nextSession: ConsultationSession = {
      ...session,
      transcription: {
        ...session.transcription,
        draftDocument,
        ...(draftProvenance ? { draftProvenance } : {})
      },
      updatedAt
    };

    this.sessions.set(id, nextSession);
    return nextSession;
  }

  async storeReportContextUpload(
    id: string,
    upload: ReportContextUploadDraft
  ): Promise<StoredReportContextUpload> {
    const stored: StoredReportContextUpload = {
      ...upload,
      id: crypto.randomUUID(),
      sessionId: id,
      uploadedAt: new Date().toISOString()
    };
    this.reportUploads.set(stored.id, stored);
    return stored;
  }

  async deleteReportContextUpload(sessionId: string, uploadId: string): Promise<void> {
    const upload = this.reportUploads.get(uploadId);

    if (upload?.sessionId === sessionId) {
      this.reportUploads.delete(uploadId);
    }
  }

  async getReportContextUploads(
    sessionId: string,
    uploadIds?: string[]
  ): Promise<StoredReportContextUpload[]> {
    const now = Date.now();
    const allSessionUploads = [...this.reportUploads.values()]
      .filter((upload) => upload.sessionId === sessionId)
      .sort((left, right) => left.uploadedAt.localeCompare(right.uploadedAt));
    const selectedUploads = uploadIds
      ? uploadIds
          .map((uploadId) => this.reportUploads.get(uploadId))
          .filter((upload): upload is StoredReportContextUpload => Boolean(upload))
      : allSessionUploads;

    return selectedUploads
      .filter((upload): upload is StoredReportContextUpload =>
        Boolean(
          upload &&
            upload.sessionId === sessionId &&
            new Date(upload.expiresAt).getTime() > now
        )
      );
  }

  async updateReportContext(
    id: string,
    reportContext: SessionReportContext
  ): Promise<ConsultationSession | null> {
    const session = this.sessions.get(id);

    if (!session) {
      return null;
    }

    const updatedAt = new Date().toISOString();
    const nextSession: ConsultationSession = {
      ...session,
      reportContext,
      updatedAt
    };

    this.sessions.set(id, nextSession);
    return nextSession;
  }

  async updateCaseStatus(
    id: string,
    caseStatus: CaseStatus
  ): Promise<ConsultationSession | null> {
    const session = this.sessions.get(id);

    if (!session) {
      return null;
    }

    const updatedAt = new Date().toISOString();
    const nextSession: ConsultationSession = {
      ...session,
      caseStatus,
      updatedAt
    };

    this.sessions.set(id, nextSession);
    return nextSession;
  }

  async closePatientCaseThroughSession(input: {
    patientPhoneNumber: string;
    doctorId: string;
    throughCreatedAt: string;
    updatedAt: string;
  }): Promise<void> {
    const normalizedTarget = normalizePhoneLookupKey(input.patientPhoneNumber);
    if (!normalizedTarget) {
      return;
    }

    for (const session of this.sessions.values()) {
      if (
        session.doctorId !== input.doctorId ||
        normalizePhoneLookupKey(session.patient.phoneNumber) !== normalizedTarget ||
        session.createdAt > input.throughCreatedAt
      ) {
        continue;
      }

      this.sessions.set(session.id, {
        ...session,
        caseStatus: "closed",
        updatedAt: input.updatedAt
      });
    }
  }

  async storeGeneratedDocument(
    id: string,
    generated: GeneratedDocumentArtifact
  ): Promise<ConsultationSession | null> {
    const session = this.sessions.get(id);

    if (!session) {
      return null;
    }

    const updatedAt = new Date().toISOString();
    const nextSession: ConsultationSession = {
      ...session,
      documentArtifact: generated.artifact,
      updatedAt
    };

    this.generatedDocuments.set(id, {
      ...generated,
      sessionId: id
    });
    this.sessions.set(id, nextSession);
    return nextSession;
  }

  async getGeneratedDocument(
    id: string
  ): Promise<(GeneratedDocumentArtifact & { artifact: DocumentArtifact }) | null> {
    return this.generatedDocuments.get(id) ?? null;
  }

  async createReportContextExtractionJob(input: {
    sessionId: string;
    uploadIds: string[];
  }): Promise<ReportContextExtractionJob> {
    const now = new Date().toISOString();
    const job: ReportContextExtractionJob = reportContextExtractionJobSchema.parse({
      id: crypto.randomUUID(),
      sessionId: input.sessionId,
      status: "queued",
      uploadIds: input.uploadIds,
      totalUnits: 0,
      completedUnits: 0,
      failedUnits: 0,
      chunks: [],
      createdAt: now,
      updatedAt: now
    });
    this.reportExtractionJobs.set(job.id, job);
    return job;
  }

  async getReportContextExtractionJob(
    sessionId: string,
    jobId: string
  ): Promise<ReportContextExtractionJob | null> {
    const job = this.reportExtractionJobs.get(jobId);
    if (!job || job.sessionId !== sessionId) {
      return null;
    }
    return job;
  }

  async updateReportContextExtractionJob(
    sessionId: string,
    jobId: string,
    updates: Partial<ReportContextExtractionJob>
  ): Promise<ReportContextExtractionJob | null> {
    const current = await this.getReportContextExtractionJob(sessionId, jobId);
    if (!current) {
      return null;
    }
    const next = reportContextExtractionJobSchema.parse({
      ...current,
      ...updates,
      id: current.id,
      sessionId: current.sessionId,
      updatedAt: updates.updatedAt ?? new Date().toISOString()
    });
    this.reportExtractionJobs.set(jobId, next);
    return next;
  }

  async findActiveReportContextExtractionJob(
    sessionId: string
  ): Promise<ReportContextExtractionJob | null> {
    const jobs = [...this.reportExtractionJobs.values()]
      .filter((job) => job.sessionId === sessionId)
      .filter((job) => job.status === "queued" || job.status === "processing")
      .sort((a, b) => b.createdAt.localeCompare(a.createdAt));
    return jobs[0] ?? null;
  }

  async recoverStaleReportContextExtractionJobs(
    staleAfterMs: number
  ): Promise<number> {
    const thresholdMs = Date.now() - staleAfterMs;
    let recoveredCount = 0;
    for (const [jobId, job] of this.reportExtractionJobs.entries()) {
      if (job.status !== "queued" && job.status !== "processing") {
        continue;
      }
      if (new Date(job.updatedAt).getTime() > thresholdMs) {
        continue;
      }
      const now = new Date().toISOString();
      const recovered = reportContextExtractionJobSchema.parse({
        ...job,
        status: "failed",
        error:
          "Report extraction timed out before completion. Please retry extraction.",
        updatedAt: now,
        finishedAt: now
      });
      this.reportExtractionJobs.set(jobId, recovered);
      recoveredCount += 1;
    }
    return recoveredCount;
  }
}

interface SessionDocument
  extends Omit<ConsultationSession, "id"> {
  _id: string;
}

interface AudioArtifactDocument
  extends Omit<SessionAudioArtifact, "id"> {
  _id: string;
  sessionId: string;
  data: Binary;
}

interface GeneratedDocumentRecord {
  artifact: DocumentArtifact;
  html: string;
  pdf: Buffer;
}

interface DocumentArtifactDocument
  extends Omit<DocumentArtifact, "id"> {
  _id: string;
  sessionId: string;
  html: string;
  pdf: Binary;
}

interface ReportContextUploadDocument
  extends Omit<StoredReportContextUpload, "id" | "data" | "expiresAt"> {
  _id: string;
  expiresAt: Date;
  data: Binary;
}

interface ReportContextExtractionJobDocument
  extends Omit<ReportContextExtractionJob, "id"> {
  _id: string;
}

interface PatientProfileDocument {
  _id: string;
  phoneNumber: string;
  name: string;
  age?: number;
  createdAt: string;
  updatedAt: string;
  version: number;
}

export class MongoSessionRepository implements SessionRepository {
  private readonly client: MongoClient;
  private readonly collection: Collection<SessionDocument>;
  private readonly audioCollection: Collection<AudioArtifactDocument>;
  private readonly documentCollection: Collection<DocumentArtifactDocument>;
  private readonly reportUploadCollection: Collection<ReportContextUploadDocument>;
  private readonly reportExtractionJobCollection: Collection<ReportContextExtractionJobDocument>;
  private readonly patientProfileCollection: Collection<PatientProfileDocument>;

  constructor(client: MongoClient, dbName = "medivoice_rewrite") {
    this.client = client;
    const database = client.db(dbName);

    this.collection = database.collection<SessionDocument>("sessions");
    this.audioCollection =
      database.collection<AudioArtifactDocument>("session_audio_artifacts");
    this.documentCollection =
      database.collection<DocumentArtifactDocument>("session_document_artifacts");
    this.reportUploadCollection =
      database.collection<ReportContextUploadDocument>("session_report_context_uploads");
    this.reportExtractionJobCollection =
      database.collection<ReportContextExtractionJobDocument>(
        "session_report_context_extraction_jobs"
      );
    this.patientProfileCollection =
      database.collection<PatientProfileDocument>("patient_profiles");
  }

  async ensureIndexes(): Promise<void> {
    await this.collection.createIndex({ createdAt: -1 });
    await this.collection.createIndex({ "patient.name": 1 });
    await this.collection.createIndex({ "patient.phoneNumber": 1 });
    await this.audioCollection.createIndex({ sessionId: 1 }, { unique: true });
    await this.documentCollection.createIndex({ sessionId: 1 }, { unique: true });
    await this.reportUploadCollection.createIndex({ sessionId: 1 });
    await this.reportUploadCollection.createIndex(
      { expiresAt: 1 },
      { expireAfterSeconds: 0 }
    );
    await this.reportExtractionJobCollection.createIndex({ sessionId: 1, createdAt: -1 });
    await this.reportExtractionJobCollection.createIndex({ sessionId: 1, status: 1 });
    await this.patientProfileCollection.createIndex({ phoneNumber: 1 }, { unique: true });
  }

  async createSession(
    draft: SessionDraft,
    options?: {
      id?: string;
      createdAt?: string;
    }
  ): Promise<ConsultationSession> {
    const session = createSessionAggregate(draft, options);
    const document: SessionDocument = {
      _id: session.id,
      doctorId: session.doctorId,
      patient: session.patient,
      doctor: session.doctor,
      status: session.status,
      caseStatus: session.caseStatus,
      createdAt: session.createdAt,
      updatedAt: session.updatedAt
    };

    await this.collection.insertOne(document);
    return session;
  }

  async getSessionById(id: string): Promise<ConsultationSession | null> {
    const document = await this.collection.findOne({ _id: id });
    return document ? parseSessionDocument(document) : null;
  }

  async listSessions(
    search: string | undefined,
    doctorId?: string
  ): Promise<ConsultationSession[]> {
    const normalizedSearch = search?.trim();
    const escapedSearch = normalizedSearch
      ? `^${escapeRegex(normalizedSearch)}`
      : undefined;

    const query = normalizedSearch
      ? {
          ...(doctorId ? { doctorId } : {}),
          $or: [
            {
              "patient.name": {
                $regex: escapedSearch,
                $options: "i"
              }
            },
            {
              "patient.phoneNumber": {
                $regex: escapedSearch,
                $options: "i"
              }
            }
          ]
        }
      : doctorId
      ? { doctorId }
      : {};

    const documents = await this.collection
      .find(query)
      .sort({ createdAt: -1 })
      .limit(maxListedSessions)
      .toArray();

    return documents.map(parseSessionDocument);
  }

  async listPatientHistorySessions(input: {
    patientPhoneNumber: string;
    currentSessionId: string;
    currentSessionCreatedAt: string;
    doctorId?: string;
    limit?: number;
  }): Promise<ConsultationSession[]> {
    const legacyPhoneRegex = buildLegacyPhoneSuffixRegex(input.patientPhoneNumber);
    const documents = await this.collection
      .find({
        _id: { $ne: input.currentSessionId },
        $or: [
          { "patient.phoneNumber": input.patientPhoneNumber },
          ...(legacyPhoneRegex
            ? [{ "patient.phoneNumber": { $regex: legacyPhoneRegex } }]
            : [])
        ],
        createdAt: { $lt: input.currentSessionCreatedAt },
        ...(input.doctorId ? { doctorId: input.doctorId } : {})
      })
      .sort({ createdAt: -1 })
      .limit(input.limit ?? 10)
      .toArray();

    return documents.map(parseSessionDocument);
  }

  async getPatientProfileByPhone(phoneNumber: string): Promise<PatientProfile | null> {
    const legacyPhoneRegex = buildLegacyPhoneSuffixRegex(phoneNumber);
    const profile = await this.patientProfileCollection.findOne({
      $or: [
        { phoneNumber },
        ...(legacyPhoneRegex ? [{ phoneNumber: { $regex: legacyPhoneRegex } }] : [])
      ]
    });
    if (!profile) {
      return null;
    }
    return {
      phoneNumber: profile.phoneNumber,
      name: profile.name,
      ...(typeof profile.age === "number" ? { age: profile.age } : {})
    };
  }

  async findMostRecentPatientForDoctorPhone(input: {
    doctorId: string;
    phoneNumber: string;
  }): Promise<{ phoneNumber: string; name: string; age?: number } | null> {
    const legacyPhoneRegex = buildLegacyPhoneSuffixRegex(input.phoneNumber);
    const document = await this.collection.findOne(
      {
        doctorId: input.doctorId,
        $or: [
          { "patient.phoneNumber": input.phoneNumber },
          ...(legacyPhoneRegex
            ? [{ "patient.phoneNumber": { $regex: legacyPhoneRegex } }]
            : [])
        ]
      },
      {
        sort: { createdAt: -1 },
        projection: {
          "patient.phoneNumber": 1,
          "patient.name": 1,
          "patient.age": 1
        }
      }
    );

    if (!document) {
      return null;
    }

    return {
      phoneNumber: document.patient.phoneNumber,
      name: document.patient.name,
      ...(typeof document.patient.age === "number"
        ? { age: document.patient.age }
        : {})
    };
  }

  async upsertPatientProfile(input: PatientProfile): Promise<void> {
    const now = new Date().toISOString();
    await this.patientProfileCollection.updateOne(
      { phoneNumber: input.phoneNumber },
      {
        $set: {
          name: input.name,
          ...(typeof input.age === "number" ? { age: input.age } : {}),
          updatedAt: now
        },
        ...(typeof input.age !== "number" ? { $unset: { age: "" } } : {}),
        $setOnInsert: {
          _id: crypto.randomUUID(),
          phoneNumber: input.phoneNumber,
          createdAt: now,
          version: 1
        },
        $inc: {
          version: 1
        }
      },
      { upsert: true }
    );
  }

  async uploadSessionAudio(
    id: string,
    draft: SessionAudioUploadDraft
  ): Promise<ConsultationSession | null> {
    const uploadedAt = new Date().toISOString();
    const audioArtifact: SessionAudioArtifact = {
      id,
      fileName: draft.fileName,
      mimeType: draft.mimeType,
      sizeBytes: draft.sizeBytes,
      durationSeconds: draft.durationSeconds,
      uploadedAt
    };
    const persistUpload = async (
      transactionSession?: ClientSession
    ): Promise<ConsultationSession | null> => {
      const existingSession = await this.collection.findOne(
        { _id: id },
        transactionSession ? { session: transactionSession } : undefined
      );
      if (!existingSession) {
        return null;
      }
      await this.audioCollection.updateOne(
        { sessionId: id },
        {
          $set: {
            sessionId: id,
            fileName: audioArtifact.fileName,
            mimeType: audioArtifact.mimeType,
            sizeBytes: audioArtifact.sizeBytes,
            durationSeconds: audioArtifact.durationSeconds,
            uploadedAt: audioArtifact.uploadedAt,
            data: new Binary(draft.data)
          },
          $setOnInsert: {
            _id: audioArtifact.id
          }
        },
        transactionSession
          ? { upsert: true, session: transactionSession }
          : { upsert: true }
      );

      await this.collection.updateOne(
        { _id: id },
        {
          $set: {
            audioArtifact,
            transcription: {
              status: "idle",
              transcriptSegments: []
            },
            updatedAt: uploadedAt
          }
        },
        transactionSession ? { session: transactionSession } : undefined
      );

      return {
        ...parseSessionDocument(existingSession),
        audioArtifact,
        transcription: {
          status: "idle",
          transcriptSegments: []
        },
        updatedAt: uploadedAt
      };
    };

    const session = this.client.startSession();

    try {
      return await session.withTransaction(() => persistUpload(session));
    } catch (error) {
      if (isMongoTransactionUnsupported(error)) {
        return persistUpload();
      }

      throw error;
    } finally {
      await session.endSession();
    }
  }

  async getSessionAudio(id: string): Promise<StoredSessionAudio | null> {
    const document = await this.audioCollection.findOne({ sessionId: id });

    if (!document) {
      return null;
    }

    return {
      fileName: document.fileName,
      mimeType: document.mimeType,
      durationSeconds: document.durationSeconds,
      data: Buffer.from(document.data.buffer)
    };
  }

  async markTranscriptionProcessing(
    id: string,
    provider: string,
    requestedAt: string,
    maxAttempts?: number,
    requestKey?: string
  ): Promise<ConsultationSession | null> {
    const existingSession = await this.collection.findOneAndUpdate(
      {
        _id: id,
        $or: [
          { transcription: { $exists: false } },
          { "transcription.status": { $ne: "processing" } }
        ]
      },
      {
        $set: {
          "transcription.status": "processing",
          "transcription.stage": "preparing_audio",
          "transcription.provider": provider,
          "transcription.requestedAt": requestedAt,
          ...(requestKey !== undefined ? { "transcription.requestKey": requestKey } : {}),
          ...(maxAttempts !== undefined
            ? { "transcription.maxAttempts": maxAttempts }
            : {}),
          updatedAt: requestedAt
        },
        $inc: {
          "transcription.attemptCount": 1
        },
        $unset: {
          "transcription.completedAt": "",
          "transcription.failedAt": "",
          "transcription.error": "",
          "transcription.errorClass": "",
          "transcription.retryable": "",
          "transcription.exhaustedRetryBudget": "",
          "transcription.requiresRecovery": "",
          "transcription.summary": "",
          "transcription.metadata": "",
          "transcription.draftDocument": "",
          ...(requestKey === undefined ? { "transcription.requestKey": "" } : {})
        }
      },
      {
        returnDocument: "after"
      }
    );

    if (existingSession) {
      return parseSessionDocument(existingSession);
    }

    return this.getSessionById(id);
  }

  async completeTranscription(
    id: string,
    draft: SessionTranscriptionCompletionDraft,
    expectedRequestKey?: string
  ): Promise<ConsultationSession | null> {
    const completedAt = new Date().toISOString();
    return this.updateSessionTranscription(id, {
      status: "completed",
      provider: draft.provider,
      requestedAt: undefined,
      completedAt,
      failedAt: undefined,
      error: undefined,
      requiresRecovery: draft.requiresRecovery,
      transcriptSegments: draft.transcriptSegments,
      summary: draft.summary,
      metadata: draft.metadata,
      draftDocument: draft.draftDocument,
      draftProvenance: draft.draftProvenance
    }, expectedRequestKey);
  }

  async failTranscription(
    id: string,
    draft: SessionTranscriptionFailureDraft,
    expectedRequestKey?: string
  ): Promise<ConsultationSession | null> {
    const failedAt = new Date().toISOString();
    return this.updateSessionTranscription(id, {
      status: "failed",
      provider: draft.provider,
      completedAt: undefined,
      failedAt,
      error: draft.error,
      errorClass: draft.errorClass,
      retryable: draft.retryable,
      exhaustedRetryBudget: draft.exhaustedRetryBudget
    }, expectedRequestKey);
  }

  async setTranscriptionStage(
    id: string,
    stage: TranscriptionStage,
    expectedRequestKey?: string
  ): Promise<ConsultationSession | null> {
    return this.updateSessionTranscription(
      id,
      {
        status: "processing",
        stage
      },
      expectedRequestKey
    );
  }

  async recoverInterruptedTranscriptions(): Promise<number> {
    const failedAt = new Date().toISOString();
    const result = await this.collection.updateMany(
      { "transcription.status": "processing" },
      {
        $set: {
          "transcription.status": "failed",
          "transcription.failedAt": failedAt,
          "transcription.error":
            "Transcription stopped when the server restarted. Please retry.",
          updatedAt: failedAt
        },
        $unset: {
          "transcription.stage": ""
        }
      }
    );

    return result.modifiedCount;
  }

  async recoverStaleProcessingTranscriptions(staleAfterMs: number): Promise<number> {
    const failedAt = new Date().toISOString();
    const cutoff = new Date(Date.now() - staleAfterMs).toISOString();
    const result = await this.collection.updateMany(
      {
        "transcription.status": "processing",
        "transcription.requestedAt": { $lte: cutoff }
      },
      {
        $set: {
          "transcription.status": "failed",
          "transcription.failedAt": failedAt,
          "transcription.error":
            "Transcription timed out while processing. Please retry.",
          updatedAt: failedAt
        },
        $unset: {
          "transcription.stage": ""
        }
      }
    );

    return result.modifiedCount;
  }

  async cleanupLegacyNullTranscriptionFields(): Promise<number> {
    const legacyOptionalFields = [
      "requestedAt",
      "completedAt",
      "failedAt",
      "error",
      "requiresRecovery",
      "summary",
      "metadata",
      "draftDocument"
    ] as const;
    let cleanedCount = 0;

    for (const field of legacyOptionalFields) {
      const result = await this.collection.updateMany(
        { [`transcription.${field}`]: null },
        { $unset: { [`transcription.${field}`]: "" } }
      );

      cleanedCount += result.modifiedCount;
    }

    return cleanedCount;
  }

  private async updateSessionTranscription(
    id: string,
    transcription: Partial<SessionTranscription> &
      Pick<SessionTranscription, "status">,
    expectedRequestKey?: string
  ): Promise<ConsultationSession | null> {
    const existingSession = await this.collection.findOne({
      _id: id,
      "transcription.status": "processing",
      ...(expectedRequestKey
        ? { "transcription.requestKey": expectedRequestKey }
        : {})
    });

    if (!existingSession) {
      return null;
    }

    const updatedAt = new Date().toISOString();
    const nextTranscription = sessionTranscriptionSchema.parse({
      ...existingSession.transcription,
      ...transcription
    });
    const {
      requestedAt,
      completedAt,
      failedAt,
      error,
      requiresRecovery,
      summary,
      metadata,
      draftDocument,
      draftProvenance,
      provider,
      status,
      transcriptSegments
    } = nextTranscription;

    const unsetFields = Object.entries({
      "transcription.requestedAt": requestedAt,
      "transcription.completedAt": completedAt,
      "transcription.failedAt": failedAt,
      "transcription.error": error,
      "transcription.errorClass": nextTranscription.errorClass,
      "transcription.retryable": nextTranscription.retryable,
      "transcription.exhaustedRetryBudget": nextTranscription.exhaustedRetryBudget,
      "transcription.stage": nextTranscription.stage,
      "transcription.requiresRecovery": requiresRecovery,
      "transcription.summary": summary,
      "transcription.metadata": metadata,
      "transcription.draftDocument": draftDocument,
      "transcription.draftProvenance": draftProvenance
    }).reduce<Record<string, "">>((accumulator, [field, value]) => {
      if (value === undefined) {
        accumulator[field] = "";
      }

      return accumulator;
    }, {});

    await this.collection.updateOne(
      { _id: id },
      {
        $set: {
          "transcription.status": status,
          "transcription.provider": provider,
          "transcription.transcriptSegments": transcriptSegments,
          ...(requestedAt !== undefined
            ? { "transcription.requestedAt": requestedAt }
            : {}),
          ...(completedAt !== undefined
            ? { "transcription.completedAt": completedAt }
            : {}),
          ...(failedAt !== undefined ? { "transcription.failedAt": failedAt } : {}),
          ...(error !== undefined ? { "transcription.error": error } : {}),
          ...(nextTranscription.stage !== undefined
            ? { "transcription.stage": nextTranscription.stage }
            : {}),
          ...(requiresRecovery !== undefined
            ? { "transcription.requiresRecovery": requiresRecovery }
            : {}),
          ...(summary !== undefined ? { "transcription.summary": summary } : {}),
          ...(metadata !== undefined ? { "transcription.metadata": metadata } : {}),
          ...(draftDocument !== undefined
            ? { "transcription.draftDocument": draftDocument }
            : {}),
          ...(draftProvenance !== undefined
            ? { "transcription.draftProvenance": draftProvenance }
            : {}),
          updatedAt
        },
        ...(Object.keys(unsetFields).length > 0 ? { $unset: unsetFields } : {})
      }
    );

    return {
      ...parseSessionDocument(existingSession),
      transcription: nextTranscription,
      updatedAt
    };
  }

  async updateDraftDocument(
    id: string,
    draftDocument: DraftConsultationDocument,
    draftProvenance?: DraftDocumentProvenance
  ): Promise<ConsultationSession | null> {
    const existingSession = await this.collection.findOne({ _id: id });

    if (!existingSession?.transcription) {
      return null;
    }

    const updatedAt = new Date().toISOString();
    const transcription = {
      ...existingSession.transcription,
      draftDocument,
      ...(draftProvenance ? { draftProvenance } : {})
    };

    await this.collection.updateOne(
      { _id: id },
      {
        $set: {
          transcription,
          updatedAt
        }
      }
    );

    return {
      ...parseSessionDocument(existingSession),
      transcription,
      updatedAt
    };
  }

  async storeReportContextUpload(
    id: string,
    upload: ReportContextUploadDraft
  ): Promise<StoredReportContextUpload> {
    const document: ReportContextUploadDocument = {
      _id: crypto.randomUUID(),
      sessionId: id,
      fileName: upload.fileName,
      mimeType: upload.mimeType,
      sizeBytes: upload.sizeBytes,
      expiresAt: new Date(upload.expiresAt),
      uploadedAt: new Date().toISOString(),
      data: new Binary(upload.data)
    };

    await this.reportUploadCollection.insertOne(document);
    return parseReportContextUploadDocument(document);
  }

  async deleteReportContextUpload(sessionId: string, uploadId: string): Promise<void> {
    await this.reportUploadCollection.deleteOne({
      _id: uploadId,
      sessionId
    });
  }

  async getReportContextUploads(
    sessionId: string,
    uploadIds?: string[]
  ): Promise<StoredReportContextUpload[]> {
    const baseQuery: {
      _id?: { $in: string[] };
      sessionId: string;
      expiresAt: { $gt: Date };
    } = {
      sessionId,
      expiresAt: { $gt: new Date() }
    };
    if (uploadIds && uploadIds.length > 0) {
      baseQuery._id = { $in: uploadIds };
    }

    const documents = await this.reportUploadCollection
      .find(baseQuery)
      .sort({ uploadedAt: 1 })
      .toArray();

    return documents.map(parseReportContextUploadDocument);
  }

  async updateReportContext(
    id: string,
    reportContext: SessionReportContext
  ): Promise<ConsultationSession | null> {
    const updatedAt = new Date().toISOString();
    const updated = await this.collection.findOneAndUpdate(
      { _id: id },
      {
        $set: {
          reportContext: sessionReportContextSchema.parse(reportContext),
          updatedAt
        }
      },
      { returnDocument: "after" }
    );

    return updated ? parseSessionDocument(updated) : null;
  }

  async updateCaseStatus(
    id: string,
    caseStatus: CaseStatus
  ): Promise<ConsultationSession | null> {
    const updatedAt = new Date().toISOString();
    const updated = await this.collection.findOneAndUpdate(
      { _id: id },
      {
        $set: {
          caseStatus,
          updatedAt
        }
      },
      {
        returnDocument: "after"
      }
    );

    return updated ? parseSessionDocument(updated) : null;
  }

  async closePatientCaseThroughSession(input: {
    patientPhoneNumber: string;
    doctorId: string;
    throughCreatedAt: string;
    updatedAt: string;
  }): Promise<void> {
    const legacyPhoneRegex = buildLegacyPhoneSuffixRegex(input.patientPhoneNumber);
    await this.collection.updateMany(
      {
        doctorId: input.doctorId,
        $or: [
          { "patient.phoneNumber": input.patientPhoneNumber },
          ...(legacyPhoneRegex
            ? [{ "patient.phoneNumber": { $regex: legacyPhoneRegex } }]
            : [])
        ],
        createdAt: { $lte: input.throughCreatedAt }
      },
      {
        $set: {
          caseStatus: "closed",
          updatedAt: input.updatedAt
        }
      }
    );
  }

  async storeGeneratedDocument(
    id: string,
    generated: GeneratedDocumentArtifact
  ): Promise<ConsultationSession | null> {
    const existingSession = await this.collection.findOne({ _id: id });

    if (!existingSession) {
      return null;
    }

    await this.documentCollection.updateOne(
      { sessionId: id },
      {
        $set: {
          sessionId: id,
          fileName: generated.artifact.fileName,
          mimeType: generated.artifact.mimeType,
          sizeBytes: generated.artifact.sizeBytes,
          createdAt: generated.artifact.createdAt,
          delivery: generated.artifact.delivery,
          html: generated.html,
          pdf: new Binary(generated.pdf)
        },
        $setOnInsert: {
          _id: generated.artifact.id
        }
      },
      { upsert: true }
    );

    const updatedAt = new Date().toISOString();

    await this.collection.updateOne(
      { _id: id },
      {
        $set: {
          documentArtifact: generated.artifact,
          updatedAt
        }
      }
    );

    return {
      ...parseSessionDocument(existingSession),
      documentArtifact: generated.artifact,
      updatedAt
    };
  }

  async getGeneratedDocument(
    id: string
  ): Promise<(GeneratedDocumentArtifact & { artifact: DocumentArtifact }) | null> {
    const document = await this.documentCollection.findOne({ sessionId: id });

    if (!document) {
      return null;
    }

    return {
      artifact: {
        id: document._id,
        fileName: document.fileName,
        mimeType: document.mimeType,
        sizeBytes: document.sizeBytes,
        createdAt: document.createdAt,
        delivery: document.delivery
      },
      html: document.html,
      pdf: Buffer.from(document.pdf.buffer)
    };
  }

  async createReportContextExtractionJob(input: {
    sessionId: string;
    uploadIds: string[];
  }): Promise<ReportContextExtractionJob> {
    const now = new Date().toISOString();
    const job = reportContextExtractionJobSchema.parse({
      id: crypto.randomUUID(),
      sessionId: input.sessionId,
      status: "queued",
      uploadIds: input.uploadIds,
      totalUnits: 0,
      completedUnits: 0,
      failedUnits: 0,
      chunks: [],
      createdAt: now,
      updatedAt: now
    });
    const document: ReportContextExtractionJobDocument = {
      _id: job.id,
      sessionId: job.sessionId,
      status: job.status,
      uploadIds: job.uploadIds,
      totalUnits: job.totalUnits,
      completedUnits: job.completedUnits,
      failedUnits: job.failedUnits,
      chunks: job.chunks,
      error: job.error,
      createdAt: job.createdAt,
      startedAt: job.startedAt,
      updatedAt: job.updatedAt,
      finishedAt: job.finishedAt
    };
    await this.reportExtractionJobCollection.insertOne(document);
    return job;
  }

  async getReportContextExtractionJob(
    sessionId: string,
    jobId: string
  ): Promise<ReportContextExtractionJob | null> {
    const document = await this.reportExtractionJobCollection.findOne({
      _id: jobId,
      sessionId
    });
    return document ? parseReportContextExtractionJobDocument(document) : null;
  }

  async updateReportContextExtractionJob(
    sessionId: string,
    jobId: string,
    updates: Partial<ReportContextExtractionJob>
  ): Promise<ReportContextExtractionJob | null> {
    const current = await this.getReportContextExtractionJob(sessionId, jobId);
    if (!current) {
      return null;
    }
    const next = reportContextExtractionJobSchema.parse({
      ...current,
      ...updates,
      id: current.id,
      sessionId: current.sessionId,
      updatedAt: updates.updatedAt ?? new Date().toISOString()
    });
    const { id: _id, ...rest } = next;
    await this.reportExtractionJobCollection.updateOne(
      { _id: jobId, sessionId },
      { $set: rest }
    );
    return next;
  }

  async findActiveReportContextExtractionJob(
    sessionId: string
  ): Promise<ReportContextExtractionJob | null> {
    const document = await this.reportExtractionJobCollection
      .find({
        sessionId,
        status: { $in: ["queued", "processing"] }
      })
      .sort({ createdAt: -1 })
      .limit(1)
      .next();
    return document ? parseReportContextExtractionJobDocument(document) : null;
  }

  async recoverStaleReportContextExtractionJobs(
    staleAfterMs: number
  ): Promise<number> {
    const threshold = new Date(Date.now() - staleAfterMs).toISOString();
    const now = new Date().toISOString();
    const result = await this.reportExtractionJobCollection.updateMany(
      {
        status: { $in: ["queued", "processing"] },
        updatedAt: { $lt: threshold }
      },
      {
        $set: {
          status: "failed",
          error:
            "Report extraction timed out before completion. Please retry extraction.",
          updatedAt: now,
          finishedAt: now
        }
      }
    );
    return result.modifiedCount;
  }
}

export function parseSessionDocument(
  document: SessionDocument
): ConsultationSession {
  return consultationSessionSchema.parse({
    id: document._id,
    doctorId: document.doctorId,
    patient: document.patient,
    doctor: document.doctor,
    status: document.status,
    caseStatus: document.caseStatus,
    audioArtifact: document.audioArtifact,
    transcription: normalizeLegacyTranscription(document.transcription),
    reportContext: normalizeLegacyReportContext(document.reportContext),
    documentArtifact: document.documentArtifact,
    createdAt: document.createdAt,
    updatedAt: document.updatedAt
  });
}

function normalizeLegacyTranscription(
  transcription: SessionDocument["transcription"]
): SessionDocument["transcription"] {
  if (!transcription) {
    return transcription;
  }

  return Object.fromEntries(
    Object.entries(transcription as Record<string, unknown>).filter(
      ([, value]) => value !== null
    )
  ) as SessionDocument["transcription"];
}

function normalizeLegacyReportContext(
  reportContext: SessionDocument["reportContext"]
): SessionDocument["reportContext"] {
  if (!reportContext) {
    return reportContext;
  }

  const normalizedChunks = (reportContext.chunks ?? []).map((chunk) =>
    Object.fromEntries(
      Object.entries((chunk ?? {}) as Record<string, unknown>).filter(
        ([, value]) => value !== null
      )
    )
  );

  return sessionReportContextSchema.parse({
    status: reportContext.status,
    chunks: normalizedChunks,
    selectedChunkIds: reportContext.selectedChunkIds ?? [],
    appliedChunkIds: reportContext.appliedChunkIds ?? [],
    updatedAt: reportContext.updatedAt,
    error: reportContext.error
  });
}

function parseReportContextUploadDocument(
  document: ReportContextUploadDocument
): StoredReportContextUpload {
  return {
    id: document._id,
    sessionId: document.sessionId,
    fileName: document.fileName,
    mimeType: document.mimeType,
    sizeBytes: document.sizeBytes,
    expiresAt: document.expiresAt.toISOString(),
    uploadedAt: document.uploadedAt,
    data: Buffer.from(document.data.buffer)
  };
}

function parseReportContextExtractionJobDocument(
  document: ReportContextExtractionJobDocument
): ReportContextExtractionJob {
  return reportContextExtractionJobSchema.parse({
    id: document._id,
    sessionId: document.sessionId,
    status: document.status,
    uploadIds: document.uploadIds,
    totalUnits: document.totalUnits,
    completedUnits: document.completedUnits,
    failedUnits: document.failedUnits,
    chunks: document.chunks,
    error: document.error,
    createdAt: document.createdAt,
    startedAt: document.startedAt,
    updatedAt: document.updatedAt,
    finishedAt: document.finishedAt
  });
}

function normalizePhoneLookupKey(value: string): string | null {
  const digits = value.replace(/\D/g, "");
  if (digits.length < 10) {
    return null;
  }
  return digits.slice(-10);
}

function buildLegacyPhoneSuffixRegex(value: string): RegExp | null {
  const normalized = normalizePhoneLookupKey(value);
  if (!normalized) {
    return null;
  }
  return new RegExp(`(?:^|\\D)${normalized}$`);
}

function escapeRegex(value: string): string {
  return value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

function isMongoTransactionUnsupported(error: unknown): boolean {
  if (error instanceof MongoServerError) {
    return (
      error.code === 20 ||
      error.codeName === "IllegalOperation" ||
      error.message.includes("Transaction numbers are only allowed")
    );
  }

  if (typeof error === "object" && error !== null) {
    const message = (error as { message?: string }).message;
    return (
      typeof message === "string" &&
      message.includes("Transaction numbers are only allowed")
    );
  }

  return false;
}
