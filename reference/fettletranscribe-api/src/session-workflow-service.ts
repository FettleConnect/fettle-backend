import {
  type CaseStatus,
  type ConsultationSession,
  type DraftConsultationDocument,
  draftConsultationDocumentSchema,
  sessionIdSchema
} from "@medivoice/shared";
import { ZodError } from "zod";

import {
  InvalidSessionStateError,
  NotFoundError,
  UnauthorizedError
} from "./errors.js";
import { DocumentService } from "./document-service.js";
import {
  hasDraftContentChanged,
  mergeDraftProvenance
} from "./session-draft-helpers.js";
import { type SessionRepository } from "./session-repository.js";

export class SessionWorkflowService {
  constructor(
    private readonly repository: SessionRepository,
    private readonly documentService = new DocumentService(),
    private readonly queueFettleSync?: (
      session: ConsultationSession,
      trigger: "transcription_completed" | "draft_updated"
    ) => void
  ) {}

  async updateDraftDocument(
    id: string,
    input: DraftConsultationDocument,
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

    if (!session.transcription?.draftDocument) {
      throw new InvalidSessionStateError(
        "Run transcription before editing the consultation draft."
      );
    }

    const normalizedDraft = this.documentService.normalizeDraftDocument(
      draftConsultationDocumentSchema.parse(input)
    );
    const draftChanged = hasDraftContentChanged(
      session.transcription.draftDocument,
      normalizedDraft
    );
    const updatedSession = await this.repository.updateDraftDocument(
      sessionId,
      normalizedDraft,
      mergeDraftProvenance(
        session.transcription?.draftProvenance,
        session.transcription?.draftDocument,
        normalizedDraft,
        "manual"
      )
    );

    if (!updatedSession) {
      throw new NotFoundError(`Session ${sessionId} was not found.`);
    }

    if (draftChanged) {
      this.queueFettleSync?.(updatedSession, "draft_updated");
    }

    return updatedSession;
  }

  async updateCaseStatus(
    id: string,
    caseStatus: CaseStatus,
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

    if (caseStatus === "closed") {
      await this.repository.closePatientCaseThroughSession({
        patientPhoneNumber: session.patient.phoneNumber,
        doctorId: session.doctorId,
        throughCreatedAt: session.createdAt,
        updatedAt: new Date().toISOString()
      });

      const updatedClosedSession = await this.repository.getSessionById(sessionId);

      if (!updatedClosedSession) {
        throw new NotFoundError(`Session ${sessionId} was not found.`);
      }

      return updatedClosedSession;
    }

    const updatedSession = await this.repository.updateCaseStatus(
      sessionId,
      caseStatus
    );

    if (!updatedSession) {
      throw new NotFoundError(`Session ${sessionId} was not found.`);
    }

    return updatedSession;
  }
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
