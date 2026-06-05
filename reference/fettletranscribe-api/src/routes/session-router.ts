import express, { Router, type Request } from "express";
import { z } from "zod";

import {
  caseStatusSchema,
  createSessionResponseSchema,
  generateSessionPdfResponseSchema,
  getPatientHistoryResponseSchema,
  getPatientProfileByPhoneResponseSchema,
  getSessionPdfDeliveryResponseSchema,
  getSessionResponseSchema,
  listSessionsResponseSchema,
  prepareSessionPdfDeliveryResponseSchema,
  submitSessionTranscriptionResponseSchema,
  updateDraftDocumentResponseSchema,
  uploadSessionAudioResponseSchema
} from "@medivoice/shared";

import { type requireDoctor as requireDoctorFn } from "../http-helpers.js";
import { buildCanonicalAudioUploadMetadata, readIdempotencyKey } from "../request-metadata.js";
import { sendSessionDownload } from "./session-artifact-helpers.js";

type SessionRouteService = {
  uploadSessionAudio: (...args: Parameters<import("../session-service.js").SessionService["uploadSessionAudio"]>) => ReturnType<import("../session-service.js").SessionService["uploadSessionAudio"]>;
  createSession: (...args: Parameters<import("../session-service.js").SessionService["createSession"]>) => ReturnType<import("../session-service.js").SessionService["createSession"]>;
  updateDraftDocument: (...args: Parameters<import("../session-service.js").SessionService["updateDraftDocument"]>) => ReturnType<import("../session-service.js").SessionService["updateDraftDocument"]>;
  requestSessionTranscription: (...args: Parameters<import("../session-service.js").SessionService["requestSessionTranscription"]>) => ReturnType<import("../session-service.js").SessionService["requestSessionTranscription"]>;
  generateSessionPdf: (...args: Parameters<import("../session-service.js").SessionService["generateSessionPdf"]>) => ReturnType<import("../session-service.js").SessionService["generateSessionPdf"]>;
  prepareSessionPdfDelivery: (...args: Parameters<import("../session-service.js").SessionService["prepareSessionPdfDelivery"]>) => ReturnType<import("../session-service.js").SessionService["prepareSessionPdfDelivery"]>;
  getSessionPdfDeliveryStatus: (...args: Parameters<import("../session-service.js").SessionService["getSessionPdfDeliveryStatus"]>) => ReturnType<import("../session-service.js").SessionService["getSessionPdfDeliveryStatus"]>;
  sendSessionPdfDelivery: (...args: Parameters<import("../session-service.js").SessionService["sendSessionPdfDelivery"]>) => ReturnType<import("../session-service.js").SessionService["sendSessionPdfDelivery"]>;
  listSessions: (...args: Parameters<import("../session-service.js").SessionService["listSessions"]>) => ReturnType<import("../session-service.js").SessionService["listSessions"]>;
  getSessionById: (...args: Parameters<import("../session-service.js").SessionService["getSessionById"]>) => ReturnType<import("../session-service.js").SessionService["getSessionById"]>;
  listPatientHistorySessions: (...args: Parameters<import("../session-service.js").SessionService["listPatientHistorySessions"]>) => ReturnType<import("../session-service.js").SessionService["listPatientHistorySessions"]>;
  getPatientProfileByPhone: (...args: Parameters<import("../session-service.js").SessionService["getPatientProfileByPhone"]>) => ReturnType<import("../session-service.js").SessionService["getPatientProfileByPhone"]>;
  updateCaseStatus: (...args: Parameters<import("../session-service.js").SessionService["updateCaseStatus"]>) => ReturnType<import("../session-service.js").SessionService["updateCaseStatus"]>;
  getTranscriptDownload: (...args: Parameters<import("../session-service.js").SessionService["getTranscriptDownload"]>) => ReturnType<import("../session-service.js").SessionService["getTranscriptDownload"]>;
  getAudioDownload: (...args: Parameters<import("../session-service.js").SessionService["getAudioDownload"]>) => ReturnType<import("../session-service.js").SessionService["getAudioDownload"]>;
  getPdfDownload: (...args: Parameters<import("../session-service.js").SessionService["getPdfDownload"]>) => ReturnType<import("../session-service.js").SessionService["getPdfDownload"]>;
};

export function createSessionRouter({
  sessionService,
  requireDoctor,
  emitFlowMetric
}: {
  sessionService: SessionRouteService;
  requireDoctor: (request: Request) => ReturnType<typeof requireDoctorFn>;
  emitFlowMetric: (
    metric: string,
    startedAt: number,
    status: "success" | "failure",
    context?: Record<string, string | number | boolean>
  ) => void;
}): Router {
  const router = Router();

  router.post(
    "/sessions/:sessionId/audio",
    express.raw({
      type: "audio/*",
      limit: "50mb"
    }),
    async (request, response, next) => {
      const startedAt = Date.now();
      try {
        const doctor = await requireDoctor(request);
        const data = Buffer.isBuffer(request.body) ? request.body : Buffer.alloc(0);
        const sizeBytes = data.length;
        const canonicalAudio = buildCanonicalAudioUploadMetadata(request, data);
        const session = await sessionService.uploadSessionAudio(
          request.params.sessionId,
          {
            fileName: canonicalAudio.fileName,
            mimeType: canonicalAudio.mimeType,
            sizeBytes,
            durationSeconds: canonicalAudio.durationSeconds,
            data
          },
          doctor.id
        );
        emitFlowMetric("session.audio_upload", startedAt, "success", {
          sessionId: request.params.sessionId,
          doctorId: doctor.id,
          sizeBytes
        });

        response
          .status(201)
          .json(uploadSessionAudioResponseSchema.parse({ session }));
      } catch (error) {
        emitFlowMetric("session.audio_upload", startedAt, "failure", {
          sessionId: request.params.sessionId
        });
        next(error);
      }
    }
  );

  router.post("/sessions", async (request, response, next) => {
    try {
      const doctor = await requireDoctor(request);
      const session = await sessionService.createSession(request.body, doctor);
      response.status(201).json(createSessionResponseSchema.parse({ session }));
    } catch (error) {
      next(error);
    }
  });

  router.put("/sessions/:sessionId/draft-document", async (request, response, next) => {
    try {
      const doctor = await requireDoctor(request);
      const session = await sessionService.updateDraftDocument(
        request.params.sessionId,
        request.body,
        doctor.id
      );

      response.json(updateDraftDocumentResponseSchema.parse({ session }));
    } catch (error) {
      next(error);
    }
  });

  router.post("/sessions/:sessionId/transcription", async (request, response, next) => {
    try {
      const doctor = await requireDoctor(request);
      const session = await sessionService.requestSessionTranscription(
        request.params.sessionId,
        doctor.id,
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

  router.post("/sessions/:sessionId/pdf", async (request, response, next) => {
    const startedAt = Date.now();
    try {
      const doctor = await requireDoctor(request);
      const session = await sessionService.generateSessionPdf(
        request.params.sessionId,
        doctor.id
      );
      emitFlowMetric("session.pdf_generate", startedAt, "success", {
        sessionId: request.params.sessionId,
        doctorId: doctor.id
      });

      response.json(generateSessionPdfResponseSchema.parse({ session }));
    } catch (error) {
      emitFlowMetric("session.pdf_generate", startedAt, "failure", {
        sessionId: request.params.sessionId
      });
      next(error);
    }
  });

  router.put("/sessions/:sessionId/pdf-delivery", async (request, response, next) => {
    try {
      const doctor = await requireDoctor(request);
      const delivery = await sessionService.prepareSessionPdfDelivery(
        request.params.sessionId,
        request.body,
        doctor.id
      );

      response.json(prepareSessionPdfDeliveryResponseSchema.parse({ delivery }));
    } catch (error) {
      next(error);
    }
  });

  router.get("/sessions/:sessionId/pdf-delivery", async (request, response, next) => {
    try {
      const doctor = await requireDoctor(request);
      const delivery = await sessionService.getSessionPdfDeliveryStatus(
        request.params.sessionId,
        doctor.id
      );

      response.json(getSessionPdfDeliveryResponseSchema.parse({ delivery }));
    } catch (error) {
      next(error);
    }
  });

  router.post("/sessions/:sessionId/pdf-delivery/send", async (request, response, next) => {
    const startedAt = Date.now();
    try {
      const doctor = await requireDoctor(request);
      const delivery = await sessionService.sendSessionPdfDelivery(
        request.params.sessionId,
        request.body,
        doctor.id
      );
      emitFlowMetric("session.pdf_delivery_send", startedAt, "success", {
        sessionId: request.params.sessionId,
        doctorId: doctor.id,
        jobCount: delivery.jobs.length
      });

      response.json(prepareSessionPdfDeliveryResponseSchema.parse({ delivery }));
    } catch (error) {
      emitFlowMetric("session.pdf_delivery_send", startedAt, "failure", {
        sessionId: request.params.sessionId
      });
      next(error);
    }
  });

  router.get("/sessions", async (request, response, next) => {
    try {
      const doctor = await requireDoctor(request);
      const sessions = await sessionService.listSessions({
        search:
          typeof request.query.search === "string" &&
          request.query.search.trim().length > 0
            ? request.query.search.trim()
            : undefined
      }, doctor.id);

      response.json(listSessionsResponseSchema.parse({ sessions }));
    } catch (error) {
      next(error);
    }
  });

  router.get("/sessions/:sessionId", async (request, response, next) => {
    try {
      const doctor = await requireDoctor(request);
      const session = await sessionService.getSessionById(
        request.params.sessionId,
        doctor.id
      );

      response.json(getSessionResponseSchema.parse({ session }));
    } catch (error) {
      next(error);
    }
  });

  router.get("/sessions/:sessionId/patient-history", async (request, response, next) => {
    try {
      const doctor = await requireDoctor(request);
      const sessions = await sessionService.listPatientHistorySessions(
        request.params.sessionId,
        doctor.id
      );

      response.json(getPatientHistoryResponseSchema.parse({ sessions }));
    } catch (error) {
      next(error);
    }
  });

  router.get("/patients/profile-by-phone", async (request, response, next) => {
    try {
      const doctor = await requireDoctor(request);
      const query = z.object({ phone: z.string().trim().regex(/^\d{10}$/) }).parse(request.query);
      const profile = await sessionService.getPatientProfileByPhone(
        query.phone,
        doctor.id
      );
      response.json(
        getPatientProfileByPhoneResponseSchema.parse({
          found: Boolean(profile),
          ...(profile ? { profile } : {})
        })
      );
    } catch (error) {
      next(error);
    }
  });

  router.patch("/sessions/:sessionId/case-status", async (request, response, next) => {
    try {
      const doctor = await requireDoctor(request);
      const parsedBody = z.object({
        caseStatus: caseStatusSchema
      }).parse(request.body);
      const session = await sessionService.updateCaseStatus(
        request.params.sessionId,
        parsedBody.caseStatus,
        doctor.id
      );

      response.json(getSessionResponseSchema.parse({ session }));
    } catch (error) {
      next(error);
    }
  });

  router.get("/sessions/:sessionId/artifacts/transcript", async (request, response, next) => {
    try {
      const doctor = await requireDoctor(request);
      const download = await sessionService.getTranscriptDownload(
        request.params.sessionId,
        doctor.id
      );

      sendSessionDownload(response, download, { noStore: true });
    } catch (error) {
      next(error);
    }
  });

  router.get("/sessions/:sessionId/artifacts/audio", async (request, response, next) => {
    try {
      const doctor = await requireDoctor(request);
      const download = await sessionService.getAudioDownload(
        request.params.sessionId,
        doctor.id
      );

      sendSessionDownload(response, download, { noStore: true });
    } catch (error) {
      next(error);
    }
  });

  router.get("/sessions/:sessionId/artifacts/pdf", async (request, response, next) => {
    try {
      const doctor = await requireDoctor(request);
      const download = await sessionService.getPdfDownload(
        request.params.sessionId,
        doctor.id
      );
      const disposition =
        request.query.disposition === "inline" ? "inline" : "attachment";

      sendSessionDownload(response, download, {
        disposition,
        noStore: true
      });
    } catch (error) {
      next(error);
    }
  });

  return router;
}
