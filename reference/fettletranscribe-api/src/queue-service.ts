import {
  type ConsultationSession,
  type DoctorProfile,
  type IntakeLink,
  type PatientProfile,
  type QueueEntry,
  type QueueEntryStatus,
  type SubmitPublicIntakeRequest,
  submitPublicIntakeRequestSchema
} from "@medivoice/shared";
import { z } from "zod";

import {
  InvalidSessionStateError,
  NotFoundError
} from "./errors.js";
import { type QueueRepository } from "./queue-repository.js";
import { SessionService } from "./session-service.js";

const queueEntryIdSchema = z.uuid();
const dedupeWindowMs = 5 * 60 * 1000;

export class QueueService {
  constructor(
    private readonly repository: QueueRepository,
    private readonly sessionService: SessionService
  ) {}

  async getDoctorIntakeLink(doctor: DoctorProfile): Promise<IntakeLink> {
    return this.repository.getOrCreateDefaultIntakeLink({
      doctorId: doctor.id,
      doctorName: doctor.name
    });
  }

  async getPublicIntakeLink(token: string): Promise<IntakeLink> {
    const intakeLink = await this.repository.getPublicIntakeLinkByToken(
      normalizeToken(token)
    );

    if (!intakeLink) {
      throw new NotFoundError("This intake link is not available.");
    }

    return intakeLink;
  }

  async submitPublicIntake(
    token: string,
    input: SubmitPublicIntakeRequest
  ): Promise<QueueEntry> {
    const intakeLink = await this.getPublicIntakeLink(token);
    const draft = submitPublicIntakeRequestSchema.parse(input);
    const existing = await this.repository.findRecentWaitingEntryByPhone(
      intakeLink.doctorId,
      draft.patient.phoneNumber
    );

    if (existing) {
      const submittedAtMs = new Date(existing.submittedAt).getTime();

      if (Number.isFinite(submittedAtMs) && Date.now() - submittedAtMs <= dedupeWindowMs) {
        return existing;
      }
    }

    return this.repository.createQueueEntry({
      doctorId: intakeLink.doctorId,
      intakeLinkId: intakeLink.id,
      intakeLabel: intakeLink.label,
      patient: draft.patient,
      source: "qr"
    });
  }

  async getPublicPatientProfileByPhone(
    token: string,
    phoneNumber: string
  ): Promise<PatientProfile | null> {
    const intakeLink = await this.getPublicIntakeLink(token);
    return this.sessionService.getPatientProfileByPhone(
      phoneNumber,
      intakeLink.doctorId
    );
  }

  async listQueueEntries(doctorId: string): Promise<QueueEntry[]> {
    return this.repository.listQueueEntries(doctorId);
  }

  async listQueueEntriesForAdmin(input?: {
    doctorId?: string;
    status?: QueueEntryStatus;
    search?: string;
  }): Promise<QueueEntry[]> {
    return this.repository.listQueueEntriesForAdmin(input);
  }

  async getQueueEntryByIdForAdmin(queueEntryId: string): Promise<QueueEntry> {
    const entryId = queueEntryIdSchema.parse(queueEntryId);
    const entry = await this.repository.getQueueEntryById(entryId);

    if (!entry) {
      throw new NotFoundError("The waiting patient could not be found.");
    }

    return entry;
  }

  async listIntakeLinksForAdmin(doctorId?: string): Promise<IntakeLink[]> {
    return this.repository.listIntakeLinks(doctorId);
  }

  async getIntakeLinkByIdForAdmin(intakeLinkId: string): Promise<IntakeLink> {
    const intakeLink = await this.repository.getIntakeLinkById(
      z.string().uuid().parse(intakeLinkId)
    );

    if (!intakeLink) {
      throw new NotFoundError("Intake link not found.");
    }

    return intakeLink;
  }

  async updateIntakeLinkForAdmin(
    intakeLinkId: string,
    input: {
      label?: string;
      isActive?: boolean;
    }
  ): Promise<IntakeLink> {
    const parsedInput = z
      .object({
        label: z.string().trim().min(1).optional(),
        isActive: z.boolean().optional()
      })
      .parse(input);
    const intakeLink = await this.repository.updateIntakeLink(
      z.string().uuid().parse(intakeLinkId),
      {
        ...(parsedInput.label ? { label: parsedInput.label } : {}),
        ...(typeof parsedInput.isActive === "boolean"
          ? { isActive: parsedInput.isActive }
          : {})
      }
    );

    if (!intakeLink) {
      throw new NotFoundError("Intake link not found.");
    }

    return intakeLink;
  }

  async acceptQueueEntry(
    queueEntryId: string,
    doctor: DoctorProfile
  ): Promise<{
    entry: QueueEntry;
    session: ConsultationSession;
  }> {
    const entryId = queueEntryIdSchema.parse(queueEntryId);
    const queueEntry = await this.repository.getQueueEntryById(entryId);

    if (!queueEntry) {
      throw new NotFoundError("The waiting patient could not be found.");
    }

    if (queueEntry.doctorId !== doctor.id) {
      throw new NotFoundError("The waiting patient could not be found.");
    }

    if (queueEntry.status === "accepted" && queueEntry.sessionId) {
      return this.resolveAcceptedQueueEntry(queueEntry, doctor);
    }

    if (queueEntry.status !== "waiting") {
      throw new InvalidSessionStateError(
        "This patient is no longer waiting in the queue."
      );
    }

    const nextSessionId = queueEntry.sessionId ?? queueEntry.id;
    const acceptedAt = new Date().toISOString();
    const updatedEntry = await this.repository.acceptQueueEntry(
      queueEntry.id,
      nextSessionId,
      acceptedAt
    );

    if (!updatedEntry) {
      throw new NotFoundError("The waiting patient could not be found.");
    }

    if (updatedEntry.acceptedAt !== acceptedAt) {
      return this.resolveAcceptedQueueEntry(updatedEntry, doctor);
    }

    const session = await this.ensureQueueSession(updatedEntry, doctor);

    return {
      entry: updatedEntry,
      session
    };
  }

  async markQueueEntryNoShow(
    queueEntryId: string,
    doctor: DoctorProfile
  ): Promise<QueueEntry> {
    return this.updateWaitingQueueEntryStatus(queueEntryId, doctor, "no_show");
  }

  async cancelQueueEntry(
    queueEntryId: string,
    doctor: DoctorProfile
  ): Promise<QueueEntry> {
    return this.updateWaitingQueueEntryStatus(queueEntryId, doctor, "cancelled");
  }

  private async resolveAcceptedQueueEntry(
    queueEntry: QueueEntry,
    doctor: DoctorProfile
  ): Promise<{
    entry: QueueEntry;
    session: ConsultationSession;
  }> {
    if (!queueEntry.sessionId) {
      throw new InvalidSessionStateError(
        "This patient was accepted, but the consultation session is missing."
      );
    }

    const session = await this.ensureQueueSession(queueEntry, doctor);

    return {
      entry: queueEntry,
      session
    };
  }

  private async ensureQueueSession(
    queueEntry: QueueEntry,
    doctor: DoctorProfile
  ): Promise<ConsultationSession> {
    if (!queueEntry.sessionId) {
      throw new InvalidSessionStateError(
        "This queue entry is missing its consultation session."
      );
    }

    try {
      return await this.sessionService.getSessionById(queueEntry.sessionId, doctor.id);
    } catch (error) {
      if (!(error instanceof NotFoundError)) {
        throw error;
      }
    }

    return this.sessionService.createSession(
      {
        doctorId: doctor.id,
        doctor: {
          name: doctor.name,
          ...(doctor.phoneNumber ? { phoneNumber: doctor.phoneNumber } : {}),
          ...(doctor.email ? { email: doctor.email } : {})
        },
        patient: queueEntry.patient
      },
      undefined,
      {
        id: queueEntry.sessionId
      }
    );
  }

  private async updateWaitingQueueEntryStatus(
    queueEntryId: string,
    doctor: DoctorProfile,
    status: Extract<QueueEntryStatus, "cancelled" | "no_show">
  ): Promise<QueueEntry> {
    const entryId = queueEntryIdSchema.parse(queueEntryId);
    const queueEntry = await this.repository.getQueueEntryById(entryId);

    if (!queueEntry || queueEntry.doctorId !== doctor.id) {
      throw new NotFoundError("The waiting patient could not be found.");
    }

    if (queueEntry.status !== "waiting") {
      throw new InvalidSessionStateError(
        "This patient is no longer waiting in the queue."
      );
    }

    const updatedEntry = await this.repository.updateQueueEntryStatus(
      queueEntry.id,
      status,
      new Date().toISOString()
    );

    if (!updatedEntry) {
      throw new InvalidSessionStateError(
        "This patient is no longer waiting in the queue."
      );
    }

    return updatedEntry;
  }
}

function normalizeToken(token: string): string {
  return z.string().trim().min(1).parse(token);
}
