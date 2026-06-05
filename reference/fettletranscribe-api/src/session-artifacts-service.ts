import {
  type ConsultationSession,
  type PrepareSessionPdfDeliveryRequest,
  type SessionPdfDeliveryStatus
} from "@medivoice/shared";

import {
  InvalidSessionStateError,
  NotFoundError,
  UnauthorizedError
} from "./errors.js";
import { DocumentService } from "./document-service.js";
import { type PdfDeliveryService } from "./pdf-delivery-service.js";
import { type SessionRepository } from "./session-repository.js";

export class SessionArtifactsService {
  constructor(
    private readonly repository: SessionRepository,
    private readonly documentService = new DocumentService(),
    private readonly pdfDeliveryService?: PdfDeliveryService
  ) {}

  async generateSessionPdf(id: string, doctorId?: string): Promise<ConsultationSession> {
    const sessionId = parseSessionId(id);
    const session = await this.repository.getSessionById(sessionId);

    if (!session) {
      throw new NotFoundError(`Session ${sessionId} was not found.`);
    }

    if (doctorId) {
      assertSessionOwnership(session, doctorId);
    }

    const draftDocument = session.transcription?.draftDocument;

    if (!draftDocument) {
      throw new InvalidSessionStateError(
        "Run transcription before generating the consultation PDF."
      );
    }

    const generated = this.documentService.generatePdfArtifact({
      session,
      draftDocument,
      ...(session.transcription?.metadata
        ? { metadata: session.transcription.metadata }
        : {})
    });
    const updatedSession = await this.repository.storeGeneratedDocument(
      sessionId,
      generated
    );

    if (!updatedSession) {
      throw new NotFoundError(`Session ${sessionId} was not found.`);
    }

    await this.pdfDeliveryService?.enqueueForSession(updatedSession);

    return updatedSession;
  }

  async prepareSessionPdfDelivery(
    id: string,
    input: PrepareSessionPdfDeliveryRequest,
    doctorId?: string
  ): Promise<SessionPdfDeliveryStatus> {
    if (!this.pdfDeliveryService) {
      throw new InvalidSessionStateError(
        "PDF delivery has not been configured for this environment."
      );
    }

    const session = await this.getOwnedSession(id, doctorId);
    return this.pdfDeliveryService.prepareSessionDelivery(session, input);
  }

  async getSessionPdfDeliveryStatus(
    id: string,
    doctorId?: string
  ): Promise<SessionPdfDeliveryStatus> {
    if (!this.pdfDeliveryService) {
      throw new InvalidSessionStateError(
        "PDF delivery has not been configured for this environment."
      );
    }

    await this.getOwnedSession(id, doctorId);
    return this.pdfDeliveryService.getSessionDeliveryStatus(id);
  }

  async sendSessionPdfDelivery(
    id: string,
    input: PrepareSessionPdfDeliveryRequest,
    doctorId?: string
  ): Promise<SessionPdfDeliveryStatus> {
    if (!this.pdfDeliveryService) {
      throw new InvalidSessionStateError(
        "PDF delivery has not been configured for this environment."
      );
    }

    const session = await this.getOwnedSession(id, doctorId);

    if (!session.documentArtifact) {
      throw new InvalidSessionStateError(
        "Generate the consultation PDF before sending it on WhatsApp."
      );
    }

    await this.pdfDeliveryService.prepareSessionDelivery(session, input);
    await this.pdfDeliveryService.enqueueForSession(session);

    return this.pdfDeliveryService.getSessionDeliveryStatus(session.id);
  }

  async getTranscriptDownload(
    id: string,
    doctorId?: string
  ): Promise<{ fileName: string; mimeType: string; data: Buffer }> {
    const session = await this.getOwnedSession(id, doctorId);

    if (!session.transcription?.transcriptSegments.length) {
      throw new InvalidSessionStateError(
        "Generate a transcription before downloading the transcript."
      );
    }

    const pdf = this.documentService.generateTranscriptPdf({
      session,
      transcription: session.transcription
    });

    return {
      fileName: `${session.id}-transcript.pdf`,
      mimeType: "application/pdf",
      data: pdf
    };
  }

  async getAudioDownload(
    id: string,
    doctorId?: string
  ): Promise<{ fileName: string; mimeType: string; data: Buffer }> {
    const sessionId = parseSessionId(id);
    const session = await this.repository.getSessionById(sessionId);

    if (!session) {
      throw new NotFoundError(`Session ${sessionId} was not found.`);
    }

    if (doctorId) {
      assertSessionOwnership(session, doctorId);
    }

    const audio = await this.repository.getSessionAudio(sessionId);

    if (!audio) {
      throw new InvalidSessionStateError(
        "Upload consultation audio before downloading it."
      );
    }

    return {
      fileName: audio.fileName,
      mimeType: audio.mimeType,
      data: audio.data
    };
  }

  async getPdfDownload(
    id: string,
    doctorId?: string
  ): Promise<{ fileName: string; mimeType: string; data: Buffer }> {
    const sessionId = parseSessionId(id);
    const session = await this.repository.getSessionById(sessionId);

    if (!session) {
      throw new NotFoundError(`Session ${sessionId} was not found.`);
    }

    if (doctorId) {
      assertSessionOwnership(session, doctorId);
    }

    const generated = await this.repository.getGeneratedDocument(sessionId);

    if (!generated) {
      throw new InvalidSessionStateError(
        "Generate the consultation PDF before downloading it."
      );
    }

    return {
      fileName: generated.artifact.fileName,
      mimeType: generated.artifact.mimeType,
      data: generated.pdf
    };
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
}

function assertSessionOwnership(
  session: ConsultationSession,
  doctorId: string
): void {
  if (session.doctorId !== doctorId) {
    throw new UnauthorizedError("This consultation belongs to another doctor.");
  }
}

function parseSessionId(id: string): string {
  return id;
}
