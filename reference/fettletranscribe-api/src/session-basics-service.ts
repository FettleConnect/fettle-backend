import {
  type ConsultationSession,
  type CreateSessionRequest,
  type DoctorProfile,
  type ListSessionsQuery,
  type PatientHistorySession,
  type PatientProfile,
  type SessionDraft,
  createSessionRequestSchema,
  listSessionsQuerySchema,
  sessionDraftSchema
} from "@medivoice/shared";

import { NotFoundError, UnauthorizedError } from "./errors.js";
import { emitKpi } from "./kpi.js";
import { logError } from "./logger.js";
import { type SessionRepository } from "./session-repository.js";

export class SessionBasicsService {
  constructor(private readonly repository: SessionRepository) {}

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
    if (!doctor && isSessionDraft(input)) {
      const parsedDraft = sessionDraftSchema.parse(input);
      const session = await this.repository.createSession(parsedDraft, options);
      await this.upsertPatientProfileBestEffort(parsedDraft.patient, parsedDraft.doctorId);
      return session;
    }

    if (!doctor) {
      throw new UnauthorizedError("Please log in to create a consultation.");
    }

    const draft = createSessionRequestSchema.parse(input);
    const session = await this.repository.createSession(
      {
        ...draft,
        doctorId: doctor.id,
        doctor: {
          name: doctor.name,
          ...(doctor.phoneNumber ? { phoneNumber: doctor.phoneNumber } : {}),
          ...(doctor.email ? { email: doctor.email } : {})
        }
      },
      options
    );
    await this.upsertPatientProfileBestEffort(draft.patient, doctor.id);
    return session;
  }

  async getPatientProfileByPhone(
    phoneNumber: string,
    doctorId?: string
  ): Promise<PatientProfile | null> {
    const startedAt = Date.now();
    const normalizedPhone = parseTenDigitPhone(phoneNumber);
    try {
      const profile = doctorId
        ? await this.repository.findMostRecentPatientForDoctorPhone({
            doctorId,
            phoneNumber: normalizedPhone
          })
        : await this.repository.getPatientProfileByPhone(normalizedPhone);
      emitKpi(
        "patient_profile.lookup",
        "success",
        {
          elapsedMs: Date.now() - startedAt,
          outcome: profile ? "found" : "not_found"
        },
        { scope: "session" }
      );
      return profile;
    } catch (error) {
      emitKpi(
        "patient_profile.lookup",
        "failure",
        {
          elapsedMs: Date.now() - startedAt
        },
        { scope: "session" }
      );
      throw error;
    }
  }

  async getSessionById(id: string, doctorId?: string): Promise<ConsultationSession> {
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

  async listSessions(
    query: ListSessionsQuery,
    doctorId?: string
  ): Promise<ConsultationSession[]> {
    const { search } = listSessionsQuerySchema.parse(query);
    return this.repository.listSessions(search, doctorId);
  }

  async listPatientHistorySessions(
    id: string,
    doctorId?: string
  ): Promise<PatientHistorySession[]> {
    const session = await this.getSessionById(id, doctorId);
    const historySessions = await this.repository.listPatientHistorySessions({
      currentSessionId: session.id,
      currentSessionCreatedAt: session.createdAt,
      patientPhoneNumber: session.patient.phoneNumber,
      ...(doctorId ? { doctorId } : {}),
      limit: 10
    });

    return historySessions.map((historySession) => ({
      id: historySession.id,
      caseStatus: historySession.caseStatus,
      patientName: historySession.patient.name,
      patientPhoneNumber: historySession.patient.phoneNumber,
      doctorName: historySession.doctor.name,
      createdAt: historySession.createdAt,
      updatedAt: historySession.updatedAt,
      ...(historySession.transcription?.draftDocument?.clinicalSummary
        ? {
            clinicalSummary:
              historySession.transcription.draftDocument.clinicalSummary
          }
        : {}),
      ...(historySession.transcription?.draftDocument?.chiefComplaint
        ? {
            chiefComplaint:
              historySession.transcription.draftDocument.chiefComplaint
          }
        : {}),
      medicationCount:
        historySession.transcription?.draftDocument?.medications.length ?? 0,
      testCount:
        historySession.transcription?.draftDocument?.testsPrescribed.length ?? 0,
      hasDocument: Boolean(historySession.documentArtifact)
    }));
  }

  private async upsertPatientProfileBestEffort(
    patient: { name: string; phoneNumber: string; age?: number | undefined },
    doctorId: string
  ): Promise<void> {
    const startedAt = Date.now();
    const normalizedPhoneNumber = parseTenDigitPhone(patient.phoneNumber);
    try {
      await this.repository.upsertPatientProfile({
        phoneNumber: normalizedPhoneNumber,
        name: patient.name,
        ...(typeof patient.age === "number" ? { age: patient.age } : {})
      });
      emitKpi(
        "patient_profile.upsert",
        "success",
        {
          elapsedMs: Date.now() - startedAt,
          doctorId
        },
        { scope: "session" }
      );
    } catch (error) {
      emitKpi(
        "patient_profile.upsert",
        "failure",
        {
          elapsedMs: Date.now() - startedAt,
          doctorId
        },
        { scope: "session" }
      );
      logError(
        "patient_profile_upsert_failed",
        {
          doctorId,
          patientPhoneNumber: normalizedPhoneNumber
        },
        { scope: "session", error }
      );
    }
  }
}

function parseTenDigitPhone(value: string): string {
  return value.replace(/\D/g, "").slice(-10);
}

function assertSessionOwnership(session: ConsultationSession, doctorId: string): void {
  if (session.doctorId !== doctorId) {
    throw new UnauthorizedError("This consultation belongs to another doctor.");
  }
}

function isSessionDraft(input: unknown): input is SessionDraft {
  if (typeof input !== "object" || input === null) {
    return false;
  }

  return "doctorId" in input && "doctor" in input && "patient" in input;
}

function parseSessionId(id: string): string {
  return id;
}
