import { Router, type Response } from "express";
import { z } from "zod";

import {
  createSessionResponseSchema,
  generateSessionPdfResponseSchema,
  getSessionPdfDeliveryResponseSchema,
  getSessionResponseSchema,
  listSessionsResponseSchema,
  prepareSessionPdfDeliveryResponseSchema,
  submitSessionTranscriptionResponseSchema,
  updateDraftDocumentResponseSchema,
  type PdfDeliveryJob
} from "@medivoice/shared";

import { InvalidSessionStateError, NotFoundError } from "../errors.js";
import { type AuthService } from "../auth-service.js";
import { readIdempotencyKey } from "../request-metadata.js";

type AdminSessionRouteService = {
  createSession: (...args: Parameters<import("../session-service.js").SessionService["createSession"]>) => ReturnType<import("../session-service.js").SessionService["createSession"]>;
  listSessions: (...args: Parameters<import("../session-service.js").SessionService["listSessions"]>) => ReturnType<import("../session-service.js").SessionService["listSessions"]>;
  getSessionById: (...args: Parameters<import("../session-service.js").SessionService["getSessionById"]>) => ReturnType<import("../session-service.js").SessionService["getSessionById"]>;
  updateDraftDocument: (...args: Parameters<import("../session-service.js").SessionService["updateDraftDocument"]>) => ReturnType<import("../session-service.js").SessionService["updateDraftDocument"]>;
  requestSessionTranscription: (...args: Parameters<import("../session-service.js").SessionService["requestSessionTranscription"]>) => ReturnType<import("../session-service.js").SessionService["requestSessionTranscription"]>;
  generateSessionPdf: (...args: Parameters<import("../session-service.js").SessionService["generateSessionPdf"]>) => ReturnType<import("../session-service.js").SessionService["generateSessionPdf"]>;
  getTranscriptDownload: (...args: Parameters<import("../session-service.js").SessionService["getTranscriptDownload"]>) => ReturnType<import("../session-service.js").SessionService["getTranscriptDownload"]>;
  getAudioDownload: (...args: Parameters<import("../session-service.js").SessionService["getAudioDownload"]>) => ReturnType<import("../session-service.js").SessionService["getAudioDownload"]>;
  getPdfDownload: (...args: Parameters<import("../session-service.js").SessionService["getPdfDownload"]>) => ReturnType<import("../session-service.js").SessionService["getPdfDownload"]>;
  prepareSessionPdfDelivery: (...args: Parameters<import("../session-service.js").SessionService["prepareSessionPdfDelivery"]>) => ReturnType<import("../session-service.js").SessionService["prepareSessionPdfDelivery"]>;
  getSessionPdfDeliveryStatus: (...args: Parameters<import("../session-service.js").SessionService["getSessionPdfDeliveryStatus"]>) => ReturnType<import("../session-service.js").SessionService["getSessionPdfDeliveryStatus"]>;
  sendSessionPdfDelivery: (...args: Parameters<import("../session-service.js").SessionService["sendSessionPdfDelivery"]>) => ReturnType<import("../session-service.js").SessionService["sendSessionPdfDelivery"]>;
};

const booleanQueryParamSchema = z.preprocess((value) => {
  if (typeof value === "string") {
    const normalized = value.trim().toLowerCase();

    if (normalized === "true" || normalized === "1") {
      return true;
    }

    if (normalized === "false" || normalized === "0" || normalized === "") {
      return false;
    }
  }

  return value;
}, z.boolean());

type PdfDeliveryAdminService = {
  listJobsForAdmin?: (input?: {
    sessionId?: string;
    status?: PdfDeliveryJob["status"];
    recipientType?: PdfDeliveryJob["recipientType"];
  }) => Promise<PdfDeliveryJob[]>;
  getJobByIdForAdmin?: (jobId: string) => Promise<PdfDeliveryJob | null>;
};

function sendAdminDownload(
  response: Response,
  download: {
    fileName: string;
    mimeType: string;
    data: Buffer;
  },
  disposition: "attachment" | "inline" = "attachment"
) {
  response
    .setHeader(
      "Content-Disposition",
      `${disposition}; filename="${download.fileName}"`
    )
    .type(download.mimeType)
    .send(download.data);
}

export function createInternalAdminSessionRouter({
  authService,
  sessionService,
  pdfDeliveryService
}: {
  authService: AuthService;
  sessionService: AdminSessionRouteService;
  pdfDeliveryService?: PdfDeliveryAdminService;
}): Router {
  const router = Router();

  router.post("/internal/admin/sessions", async (request, response, next) => {
    try {
      const input = z.object({
        doctorId: z.string().uuid(),
        patient: z.object({
          name: z.string().trim().min(1),
          phoneNumber: z.string().trim().min(10).max(20),
          age: z.number().int().nonnegative().max(130).optional()
        })
      }).parse(request.body);
      const doctor = await authService.getDoctorById(input.doctorId);
      const session = await sessionService.createSession(
        {
          patient: input.patient
        },
        doctor
      );
      response.status(201).json(createSessionResponseSchema.parse({ session }));
    } catch (error) {
      next(error);
    }
  });

  router.get("/internal/admin/sessions", async (request, response, next) => {
    try {
      const query = z.object({
        search: z.string().trim().min(1).optional(),
        doctorId: z.string().uuid().optional(),
        transcriptionStatus: z.enum(["idle", "processing", "completed", "failed"]).optional(),
        hasDocument: booleanQueryParamSchema.optional()
      }).parse(request.query);
      const sessions = await sessionService.listSessions(
        {
          ...(query.search ? { search: query.search } : {})
        },
        query.doctorId
      );
      const filteredSessions = sessions
        .filter((session) =>
          query.transcriptionStatus
            ? session.transcription?.status === query.transcriptionStatus
            : true
        )
        .filter((session) =>
          typeof query.hasDocument === "boolean"
            ? Boolean(session.documentArtifact) === query.hasDocument
            : true
        );
      response.json(listSessionsResponseSchema.parse({ sessions: filteredSessions }));
    } catch (error) {
      next(error);
    }
  });

  router.get("/internal/admin/sessions/:sessionId", async (request, response, next) => {
    try {
      const session = await sessionService.getSessionById(request.params.sessionId);
      response.json(getSessionResponseSchema.parse({ session }));
    } catch (error) {
      next(error);
    }
  });

  router.get("/internal/admin/sessions/:sessionId/transcription", async (request, response, next) => {
    try {
      const session = await sessionService.getSessionById(request.params.sessionId);
      response.json({
        transcription: session.transcription ?? null
      });
    } catch (error) {
      next(error);
    }
  });

  router.put("/internal/admin/sessions/:sessionId/draft-document", async (request, response, next) => {
    try {
      const session = await sessionService.updateDraftDocument(
        request.params.sessionId,
        request.body
      );
      response.json(updateDraftDocumentResponseSchema.parse({ session }));
    } catch (error) {
      next(error);
    }
  });

  router.post("/internal/admin/sessions/:sessionId/transcription", async (request, response, next) => {
    try {
      const session = await sessionService.requestSessionTranscription(
        request.params.sessionId,
        undefined,
        readIdempotencyKey(request),
        response.locals.requestId as string | undefined
      );

      response.status(202).json(
        submitSessionTranscriptionResponseSchema.parse({
          session
        })
      );
    } catch (error) {
      next(error);
    }
  });

  router.post("/internal/admin/sessions/:sessionId/pdf", async (request, response, next) => {
    try {
      const session = await sessionService.generateSessionPdf(
        request.params.sessionId
      );
      response.json(generateSessionPdfResponseSchema.parse({ session }));
    } catch (error) {
      next(error);
    }
  });

  router.get("/internal/admin/sessions/:sessionId/artifacts/transcript", async (request, response, next) => {
    try {
      const download = await sessionService.getTranscriptDownload(
        request.params.sessionId
      );
      sendAdminDownload(response, download);
    } catch (error) {
      next(error);
    }
  });

  router.get("/internal/admin/sessions/:sessionId/artifacts/audio", async (request, response, next) => {
    try {
      const download = await sessionService.getAudioDownload(
        request.params.sessionId
      );
      sendAdminDownload(response, download);
    } catch (error) {
      next(error);
    }
  });

  router.get("/internal/admin/sessions/:sessionId/artifacts/pdf", async (request, response, next) => {
    try {
      const download = await sessionService.getPdfDownload(request.params.sessionId);
      const disposition =
        request.query.disposition === "inline" ? "inline" : "attachment";
      sendAdminDownload(response, download, disposition);
    } catch (error) {
      next(error);
    }
  });

  router.put("/internal/admin/sessions/:sessionId/pdf-delivery", async (request, response, next) => {
    try {
      const delivery = await sessionService.prepareSessionPdfDelivery(
        request.params.sessionId,
        request.body
      );

      response.json(prepareSessionPdfDeliveryResponseSchema.parse({ delivery }));
    } catch (error) {
      next(error);
    }
  });

  router.get("/internal/admin/sessions/:sessionId/pdf-delivery", async (request, response, next) => {
    try {
      const delivery = await sessionService.getSessionPdfDeliveryStatus(
        request.params.sessionId
      );
      response.json(getSessionPdfDeliveryResponseSchema.parse({ delivery }));
    } catch (error) {
      next(error);
    }
  });

  router.post("/internal/admin/sessions/:sessionId/pdf-delivery/send", async (request, response, next) => {
    try {
      const delivery = await sessionService.sendSessionPdfDelivery(
        request.params.sessionId,
        request.body
      );
      response.json(prepareSessionPdfDeliveryResponseSchema.parse({ delivery }));
    } catch (error) {
      next(error);
    }
  });

  router.get("/internal/admin/pdf-delivery/jobs", async (request, response, next) => {
    try {
      if (!pdfDeliveryService?.listJobsForAdmin) {
        throw new InvalidSessionStateError(
          "PDF delivery has not been configured for this environment."
        );
      }
      const query = z.object({
        sessionId: z.string().uuid().optional(),
        status: z.enum([
          "pending",
          "processing",
          "queued",
          "sent",
          "delivered",
          "read",
          "failed"
        ]).optional(),
        recipientType: z.enum(["patient", "pharmacy"]).optional()
      }).parse(request.query);
      const jobs = await pdfDeliveryService.listJobsForAdmin({
        ...(query.sessionId ? { sessionId: query.sessionId } : {}),
        ...(query.status ? { status: query.status } : {}),
        ...(query.recipientType
          ? { recipientType: query.recipientType }
          : {})
      });
      response.json({ jobs });
    } catch (error) {
      next(error);
    }
  });

  router.get("/internal/admin/pdf-delivery/jobs/:jobId", async (request, response, next) => {
    try {
      if (!pdfDeliveryService?.getJobByIdForAdmin) {
        throw new InvalidSessionStateError(
          "PDF delivery has not been configured for this environment."
        );
      }
      const job = await pdfDeliveryService.getJobByIdForAdmin(request.params.jobId);

      if (!job) {
        throw new NotFoundError("PDF delivery job was not found.");
      }

      response.json({ job });
    } catch (error) {
      next(error);
    }
  });

  return router;
}
