import express, { Router, type Request } from "express";

import {
  type ConsultationSession,
  type ReportContextExtractionJob,
  applyReportContextDraftRequestSchema,
  applyReportContextDraftResponseSchema,
  createReportContextExtractionJobResponseSchema,
  extractReportContextRequestSchema,
  extractReportContextResponseSchema,
  getReportContextExtractionJobResponseSchema,
  listReportContextUploadsResponseSchema,
  suggestReportContextDraftRequestSchema,
  suggestReportContextDraftResponseSchema,
  uploadReportContextFileResponseSchema
} from "@medivoice/shared";

import {
  type requireDoctor as requireDoctorFn,
  type requireDoctorFromStreamRequest as requireDoctorFromStreamRequestFn
} from "../http-helpers.js";
import { logError } from "../logger.js";
import { readReportFileName, readReportMimeType } from "../request-metadata.js";

type ReportContextRouteService = {
  uploadReportContextFile: (...args: Parameters<import("../session-service.js").SessionService["uploadReportContextFile"]>) => ReturnType<import("../session-service.js").SessionService["uploadReportContextFile"]>;
  deleteReportContextUpload: (...args: Parameters<import("../session-service.js").SessionService["deleteReportContextUpload"]>) => ReturnType<import("../session-service.js").SessionService["deleteReportContextUpload"]>;
  listReportContextUploads: (...args: Parameters<import("../session-service.js").SessionService["listReportContextUploads"]>) => ReturnType<import("../session-service.js").SessionService["listReportContextUploads"]>;
  getReportContextUploadContent: (...args: Parameters<import("../session-service.js").SessionService["getReportContextUploadContent"]>) => ReturnType<import("../session-service.js").SessionService["getReportContextUploadContent"]>;
  extractReportContext: (...args: Parameters<import("../session-service.js").SessionService["extractReportContext"]>) => ReturnType<import("../session-service.js").SessionService["extractReportContext"]>;
  startReportContextExtractionJob: (...args: Parameters<import("../session-service.js").SessionService["startReportContextExtractionJob"]>) => ReturnType<import("../session-service.js").SessionService["startReportContextExtractionJob"]>;
  getReportContextExtractionJob: (...args: Parameters<import("../session-service.js").SessionService["getReportContextExtractionJob"]>) => ReturnType<import("../session-service.js").SessionService["getReportContextExtractionJob"]>;
  getSessionById: (...args: Parameters<import("../session-service.js").SessionService["getSessionById"]>) => ReturnType<import("../session-service.js").SessionService["getSessionById"]>;
  cancelReportContextExtractionJob: (...args: Parameters<import("../session-service.js").SessionService["cancelReportContextExtractionJob"]>) => ReturnType<import("../session-service.js").SessionService["cancelReportContextExtractionJob"]>;
  suggestReportContextDraft: (...args: Parameters<import("../session-service.js").SessionService["suggestReportContextDraft"]>) => ReturnType<import("../session-service.js").SessionService["suggestReportContextDraft"]>;
  applyReportContextDraft: (...args: Parameters<import("../session-service.js").SessionService["applyReportContextDraft"]>) => ReturnType<import("../session-service.js").SessionService["applyReportContextDraft"]>;
};

export function createReportContextRouter({
  sessionService,
  requireDoctor,
  requireDoctorFromStreamRequest
}: {
  sessionService: ReportContextRouteService;
  requireDoctor: (request: Request) => ReturnType<typeof requireDoctorFn>;
  requireDoctorFromStreamRequest: (request: Request) => ReturnType<typeof requireDoctorFromStreamRequestFn>;
}): Router {
  const router = Router();

  const attachSessionProgress = (
    job: ReportContextExtractionJob,
    session: ConsultationSession
  ): ReportContextExtractionJob => ({
    ...job,
    ...(session.reportContext?.progress
      ? { progress: session.reportContext.progress }
      : {})
  });

  const getExtractionJobWithProgress = async (
    sessionId: string,
    jobId: string,
    doctorId: string
  ): Promise<ReportContextExtractionJob> => {
    const [job, session] = await Promise.all([
      sessionService.getReportContextExtractionJob(sessionId, jobId, doctorId),
      sessionService.getSessionById(sessionId, doctorId)
    ]);
    return attachSessionProgress(job, session);
  };

  router.post(
    "/sessions/:sessionId/report-context/uploads",
    express.raw({
      type: ["application/pdf", "image/jpeg", "image/png", "image/webp"],
      limit: "15mb"
    }),
    async (request, response, next) => {
      try {
        const doctor = await requireDoctor(request);
        const upload = await sessionService.uploadReportContextFile(
          request.params.sessionId,
          {
            fileName: readReportFileName(request),
            mimeType: readReportMimeType(request),
            sizeBytes: Buffer.isBuffer(request.body) ? request.body.length : 0,
            data: Buffer.isBuffer(request.body) ? request.body : Buffer.alloc(0)
          },
          doctor.id
        );

        response
          .status(201)
          .json(uploadReportContextFileResponseSchema.parse({ upload }));
      } catch (error) {
        next(error);
      }
    }
  );

  router.delete(
    "/sessions/:sessionId/report-context/uploads/:uploadId",
    async (request, response, next) => {
      try {
        const doctor = await requireDoctor(request);
        await sessionService.deleteReportContextUpload(
          request.params.sessionId,
          request.params.uploadId,
          doctor.id
        );
        response.status(204).send();
      } catch (error) {
        next(error);
      }
    }
  );

  router.get(
    "/sessions/:sessionId/report-context/uploads",
    async (request, response, next) => {
      try {
        const doctor = await requireDoctor(request);
        const uploads = await sessionService.listReportContextUploads(
          request.params.sessionId,
          doctor.id
        );
        response.json(listReportContextUploadsResponseSchema.parse({ uploads }));
      } catch (error) {
        next(error);
      }
    }
  );

  router.get(
    "/sessions/:sessionId/report-context/uploads/:uploadId/content",
    async (request, response, next) => {
      try {
        const doctor = await requireDoctor(request);
        const upload = await sessionService.getReportContextUploadContent(
          request.params.sessionId,
          request.params.uploadId,
          doctor.id
        );

        response
          .setHeader(
            "Cache-Control",
            "no-store, no-cache, must-revalidate, proxy-revalidate"
          )
          .setHeader("Pragma", "no-cache")
          .setHeader("Expires", "0")
          .setHeader(
            "Content-Disposition",
            `inline; filename="${upload.fileName}"`
          )
          .type(upload.mimeType)
          .send(upload.data);
      } catch (error) {
        next(error);
      }
    }
  );

  router.post("/sessions/:sessionId/report-context/extract", async (request, response, next) => {
    try {
      const doctor = await requireDoctor(request);
      const input = extractReportContextRequestSchema.parse(request.body);
      const session = await sessionService.extractReportContext(
        request.params.sessionId,
        input.uploadIds,
        doctor.id
      );
      response.json(extractReportContextResponseSchema.parse({ session }));
    } catch (error) {
      next(error);
    }
  });

  router.post(
    "/sessions/:sessionId/report-context/extract-async",
    async (request, response, next) => {
      try {
        const doctor = await requireDoctor(request);
        const input = extractReportContextRequestSchema.parse(request.body);
        const job = await sessionService.startReportContextExtractionJob(
          request.params.sessionId,
          input.uploadIds,
          doctor.id
        );
        response.status(202).json(
          createReportContextExtractionJobResponseSchema.parse({ job })
        );
      } catch (error) {
        next(error);
      }
    }
  );

  router.get(
    "/sessions/:sessionId/report-context/jobs/:jobId",
    async (request, response, next) => {
      try {
        const doctor = await requireDoctor(request);
        const job = await getExtractionJobWithProgress(
          request.params.sessionId,
          request.params.jobId,
          doctor.id
        );
        response.json(getReportContextExtractionJobResponseSchema.parse({ job }));
      } catch (error) {
        next(error);
      }
    }
  );

  router.get(
    "/sessions/:sessionId/report-context/jobs/:jobId/stream",
    async (request, response, next) => {
      try {
        const doctor = await requireDoctorFromStreamRequest(request);
        const sessionId = request.params.sessionId;
        const jobId = request.params.jobId;
        await sessionService.getReportContextExtractionJob(sessionId, jobId, doctor.id);

        response.setHeader("Content-Type", "text/event-stream");
        response.setHeader("Cache-Control", "no-cache, no-transform");
        response.setHeader("Connection", "keep-alive");
        response.flushHeaders();
        response.write("retry: 2000\n\n");

        let closed = false;
        let polling = false;
        let sequence = 0;

        const writeEvent = (
          payload: Record<string, unknown>,
          eventName?: string
        ) => {
          if (closed) {
            return;
          }
          sequence += 1;
          response.write(`id: ${sequence}\n`);
          if (eventName) {
            response.write(`event: ${eventName}\n`);
          }
          response.write(`data: ${JSON.stringify(payload)}\n\n`);
        };

        const pushUpdate = async () => {
          if (closed || polling) {
            return;
          }
          polling = true;
          try {
            const job = await getExtractionJobWithProgress(
              sessionId,
              jobId,
              doctor.id
            );
            writeEvent({ job });
            if (
              job.status === "ready" ||
              job.status === "failed" ||
              job.status === "canceled"
            ) {
              writeEvent({ status: "closed", reason: "terminal_state" }, "terminal");
              clearInterval(heartbeatInterval);
              clearInterval(interval);
              response.end();
              closed = true;
            }
          } catch (error) {
            writeEvent(
              { error: "Unable to stream extraction progress." },
              "terminal"
            );
            clearInterval(heartbeatInterval);
            clearInterval(interval);
            response.end();
            closed = true;
            logError(
              "report_context_stream_failed",
              { sessionId, jobId },
              { scope: "http", error }
            );
          } finally {
            polling = false;
          }
        };

        const heartbeatInterval = setInterval(() => {
          writeEvent({ ts: new Date().toISOString() }, "heartbeat");
        }, 15_000);
        writeEvent({ ts: new Date().toISOString() }, "heartbeat");

        const interval = setInterval(() => {
          void pushUpdate();
        }, 1500);
        void pushUpdate();

        request.on("close", () => {
          closed = true;
          clearInterval(heartbeatInterval);
          clearInterval(interval);
        });
      } catch (error) {
        next(error);
      }
    }
  );

  router.post(
    "/sessions/:sessionId/report-context/jobs/:jobId/cancel",
    async (request, response, next) => {
      try {
        const doctor = await requireDoctor(request);
        const job = await sessionService.cancelReportContextExtractionJob(
          request.params.sessionId,
          request.params.jobId,
          doctor.id
        );
        response.json(getReportContextExtractionJobResponseSchema.parse({ job }));
      } catch (error) {
        next(error);
      }
    }
  );

  router.post(
    "/sessions/:sessionId/report-context/suggestions",
    async (request, response, next) => {
      try {
        const doctor = await requireDoctor(request);
        const input = suggestReportContextDraftRequestSchema.parse(request.body);
        const suggestion = await sessionService.suggestReportContextDraft(
          request.params.sessionId,
          input.chunkIds,
          input.customChunks,
          doctor.id
        );
        response.json(
          suggestReportContextDraftResponseSchema.parse(suggestion)
        );
      } catch (error) {
        next(error);
      }
    }
  );

  router.post("/sessions/:sessionId/report-context/apply", async (request, response, next) => {
    try {
      const doctor = await requireDoctor(request);
      const input = applyReportContextDraftRequestSchema.parse(request.body);
      const session = await sessionService.applyReportContextDraft(
        request.params.sessionId,
        input.chunkIds,
        input.customChunks,
        input.draftDocument,
        doctor.id
      );
      response.json(applyReportContextDraftResponseSchema.parse({ session }));
    } catch (error) {
      next(error);
    }
  });

  return router;
}
