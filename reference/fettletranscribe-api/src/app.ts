import express, {
  type Express,
  type NextFunction,
  type Request,
  type Response
} from "express";
import { randomUUID } from "node:crypto";
import { ZodError, z, type ZodIssue } from "zod";

import {
  acceptQueueEntryResponseSchema,
  caseStatusSchema,
  createSessionResponseSchema,
  createReportContextExtractionJobResponseSchema,
  applyReportContextDraftRequestSchema,
  applyReportContextDraftResponseSchema,
  extractReportContextRequestSchema,
  extractReportContextResponseSchema,
  generateSessionPdfResponseSchema,
  getPatientHistoryResponseSchema,
  getPatientProfileByPhoneResponseSchema,
  getPublicIntakeLinkResponseSchema,
  getSessionPdfDeliveryResponseSchema,
  getSessionResponseSchema,
  getReportContextExtractionJobResponseSchema,
  listDoctorsQuerySchema,
  listQueueEntriesResponseSchema,
  listReportContextUploadsResponseSchema,
  listSessionsResponseSchema,
  prepareSessionPdfDeliveryResponseSchema,
  suggestReportContextDraftRequestSchema,
  suggestReportContextDraftResponseSchema,
  submitSessionTranscriptionResponseSchema,
  updateQueueEntryResponseSchema,
  updateDraftDocumentResponseSchema,
  uploadReportContextFileResponseSchema,
  uploadSessionAudioResponseSchema,
  type PdfDeliveryJob
} from "@medivoice/shared";

import {
  AIProviderError,
  BadRequestError,
  ConflictError,
  FeatureNotReadyError,
  InvalidSessionStateError,
  LockedError,
  MalformedAIOutputError,
  NotFoundError,
  UnauthorizedError
} from "./errors.js";
import { AuthService } from "./auth-service.js";
import { InMemoryDoctorRepository } from "./doctor-repository.js";
import { DEVELOPMENT_JWT_SECRET } from "./env.js";
import {
  isInternalApiEnabled,
  requireInternalApi,
  type InternalApiActor,
  type InternalApiConfig
} from "./internal-api.js";
import { InMemoryQueueRepository } from "./queue-repository.js";
import { QueueService } from "./queue-service.js";
import { getReliabilityAlerts } from "./reliability-alerts.js";
import { InMemorySessionRepository } from "./session-repository.js";
import { SessionService } from "./session-service.js";
import { MockGeminiTranscriptionAdapter } from "./transcription-adapter.js";
import { type TwilioRequestValidator } from "./twilio-request-validator.js";
import { logError, logInfo, logWarn } from "./logger.js";
import { emitKpi } from "./kpi.js";
import { type FettleSyncService } from "./fettle-sync-service.js";
import {
  requireDoctor,
  requireDoctorFromStreamRequest
} from "./http-helpers.js";
import { createAuthRouter } from "./routes/auth-router.js";
import { createKpiRouter } from "./routes/kpi-router.js";
import { createInternalAdminDoctorRouter } from "./routes/internal-admin-doctor-router.js";
import { createInternalAdminQueueRouter } from "./routes/internal-admin-queue-router.js";
import { createInternalAdminSessionRouter } from "./routes/internal-admin-session-router.js";
import { createInternalAdminSyncRouter } from "./routes/internal-admin-sync-router.js";
import { createPdfDeliveryRouter } from "./routes/pdf-delivery-router.js";
import { createPublicRouter } from "./routes/public-router.js";
import { createQueueRouter } from "./routes/queue-router.js";
import { createReportContextRouter } from "./routes/report-context-router.js";
import { createSessionRouter } from "./routes/session-router.js";
export {
  createHealthPayload,
  formatZodErrorMessage
} from "./http-helpers.js";
import {
  createHealthPayload,
  formatZodErrorMessage
} from "./http-helpers.js";

interface AppDependencies {
  sessionService?: SessionService;
  queueService?: QueueService;
  authService?: AuthService;
  webOrigin?: string;
  useMockTranscription?: boolean;
  internalApi?: InternalApiConfig;
  pdfDeliveryService?: {
    handleProviderStatusUpdate?: (input: {
      providerMessageId: string;
      providerStatus?: string;
      errorMessage?: string;
    }) => Promise<unknown>;
    getPublicMediaAsset?: (token: string) => Promise<{
      sessionId: string;
      fileName: string;
      mimeType: string;
      data: Buffer;
    } | null>;
    listJobsForAdmin?: (input?: {
      sessionId?: string;
      status?: PdfDeliveryJob["status"];
      recipientType?: PdfDeliveryJob["recipientType"];
    }) => Promise<PdfDeliveryJob[]>;
    getJobByIdForAdmin?: (jobId: string) => Promise<PdfDeliveryJob | null>;
  };
  fettleSyncService?: FettleSyncService;
  twilioRequestValidator?: TwilioRequestValidator;
  twilioWebhookUrl?: string;
}

const SAMPLE_PDF_BUFFER = Buffer.from("%PDF-1.4\n% MediVoice sample PDF\n");

export function createApp(dependencies: AppDependencies = {}): Express {
  const app = express();
  const sessionService =
    dependencies.sessionService ??
    new SessionService(
      new InMemorySessionRepository(),
      new MockGeminiTranscriptionAdapter()
    );
  const queueService =
    dependencies.queueService ??
    new QueueService(new InMemoryQueueRepository(), sessionService);
  const authService =
    dependencies.authService ??
    new AuthService(new InMemoryDoctorRepository(), DEVELOPMENT_JWT_SECRET);
  const webOrigin = dependencies.webOrigin ?? "http://localhost:3000";
  const useMockTranscription = dependencies.useMockTranscription ?? true;
  const internalApi = dependencies.internalApi ?? {
    enabled: false,
    apiKeys: []
  };
  const pdfDeliveryService = dependencies.pdfDeliveryService;
  const fettleSyncService = dependencies.fettleSyncService;
  const twilioRequestValidator = dependencies.twilioRequestValidator;
  const twilioWebhookUrl = dependencies.twilioWebhookUrl;
  const emitFlowMetric = (
    metric: string,
    startedAt: number,
    status: "success" | "failure",
    context: Record<string, string | number | boolean> = {}
  ) => {
    emitKpi(
      metric,
      status,
      {
        elapsedMs: Date.now() - startedAt,
        ...context
      },
      { scope: "flow" }
    );
  };

  app.use((request, response, next) => {
    const requestId = randomUUID();
    const startedAt = Date.now();

    response.setHeader("X-Request-Id", requestId);
    response.locals.requestId = requestId;

    response.on("finish", () => {
      const durationMs = Date.now() - startedAt;
      const statusClass =
        response.statusCode >= 500
          ? "server_error"
          : response.statusCode >= 400
            ? "client_error"
            : "success";

      logInfo(
        "request_completed",
        {
          requestId,
          method: request.method,
          path: request.path,
          statusCode: response.statusCode,
          durationMs,
          ...(
            request.params &&
            typeof request.params.sessionId === "string"
            ? { sessionId: request.params.sessionId }
            : {})
        },
        { scope: "http" }
      );

      emitKpi(
        "http.request",
        response.statusCode >= 500 ? "failure" : "success",
        {
          requestId,
          method: request.method,
          path: request.path,
          statusCode: response.statusCode,
          statusClass,
          durationMs
        },
        { scope: "http" }
      );
    });

    next();
  });

  app.use((request, response, next) => {
    response.header("Access-Control-Allow-Origin", webOrigin);
    response.header("Vary", "Origin");
    response.header("Access-Control-Allow-Methods", "GET,POST,PUT,DELETE,OPTIONS");
    response.header(
      "Access-Control-Allow-Headers",
      "Authorization, Content-Type, X-Idempotency-Key, X-Audio-Duration-Seconds, X-Audio-File-Name, X-Report-File-Name, X-Internal-API-Key, X-Internal-Caller, X-Admin-User-Id, X-Admin-User-Email"
    );

    if (request.method === "OPTIONS") {
      response.sendStatus(204);
      return;
    }

    next();
  });
  app.use(express.json());
  app.use(express.urlencoded({ extended: false }));

  app.get("/health", (_request, response) => {
    const health = createHealthPayload();
    response.json({
      ...health,
      mockMode: useMockTranscription
    });
  });

  if (isInternalApiEnabled(internalApi)) {
    app.use("/internal", (request, response, next) => {
      try {
        response.locals.internalApiActor = requireInternalApi(
          request,
          internalApi
        );
        next();
      } catch (error) {
        next(error);
      }
    });

    app.get("/internal/health", (_request, response) => {
      const actor = response.locals.internalApiActor as
        | InternalApiActor
        | undefined;

      response.json({
        ...createHealthPayload(),
        internalApi: {
          authenticated: true,
          authenticationMethod: actor?.authenticationMethod,
          ...(actor?.caller ? { caller: actor.caller } : {}),
          ...(actor?.adminUserId ? { adminUserId: actor.adminUserId } : {}),
          ...(actor?.adminUserEmail
            ? { adminUserEmail: actor.adminUserEmail }
            : {})
        }
      });
    });

    app.get("/internal/admin/reliability/alerts", (_request, response) => {
      response.json(getReliabilityAlerts());
    });

    app.use(
      createInternalAdminSyncRouter({
        ...(fettleSyncService ? { fettleSyncService } : {})
      })
    );

    app.use(createInternalAdminDoctorRouter({ authService }));
    app.use(
      createInternalAdminQueueRouter({
        authService,
        queueService
      })
    );

    app.use(
      createInternalAdminSessionRouter({
        authService,
        sessionService,
        ...(pdfDeliveryService ? { pdfDeliveryService } : {})
      })
    );
  }

  app.use(
    createAuthRouter({
      authService,
      emitFlowMetric,
      requireDoctor: (request) => requireDoctor(request, authService)
    })
  );
  app.use(
    createQueueRouter({
      queueService,
      requireDoctor: (request) => requireDoctor(request, authService)
    })
  );

  app.use(createPublicRouter({ authService, queueService }));
  app.use(createKpiRouter());
  app.use(
    createReportContextRouter({
      sessionService,
      requireDoctor: (request) => requireDoctor(request, authService),
      requireDoctorFromStreamRequest: (request) =>
        requireDoctorFromStreamRequest(request, authService)
    })
  );
  app.use(
    createSessionRouter({
      sessionService,
      requireDoctor: (request) => requireDoctor(request, authService),
      emitFlowMetric
    })
  );

  app.use(
    createPdfDeliveryRouter({
      ...(pdfDeliveryService ? { pdfDeliveryService } : {}),
      ...(twilioRequestValidator ? { twilioRequestValidator } : {}),
      ...(twilioWebhookUrl ? { twilioWebhookUrl } : {}),
      samplePdfBuffer: SAMPLE_PDF_BUFFER
    })
  );

  app.use(
    (
      error: unknown,
      request: Request,
      response: Response,
      _next: NextFunction
    ) => {
      if (error instanceof NotFoundError) {
        logWarn(
          "request_failed",
          {
            requestId: response.locals.requestId as string | undefined,
            method: request.method,
            path: request.path,
            statusCode: 404,
            errorName: error.name
          },
          { scope: "http", error }
        );
        response.status(404).json({ error: error.message });
        return;
      }

      if (error instanceof UnauthorizedError) {
        logWarn(
          "request_failed",
          {
            requestId: response.locals.requestId as string | undefined,
            method: request.method,
            path: request.path,
            statusCode: 401,
            errorName: error.name
          },
          { scope: "http", error }
        );
        response.status(401).json({
          error: error.message,
          ...(error.code ? { code: error.code } : {})
        });
        return;
      }

      if (error instanceof ConflictError) {
        logWarn(
          "request_failed",
          {
            requestId: response.locals.requestId as string | undefined,
            method: request.method,
            path: request.path,
            statusCode: 409,
            errorName: error.name
          },
          { scope: "http", error }
        );
        response.status(409).json({
          error: error.message,
          ...(error.code ? { code: error.code } : {})
        });
        return;
      }

      if (error instanceof LockedError) {
        logWarn(
          "request_failed",
          {
            requestId: response.locals.requestId as string | undefined,
            method: request.method,
            path: request.path,
            statusCode: 423,
            errorName: error.name
          },
          { scope: "http", error }
        );
        response.status(423).json({
          error: error.message,
          ...(error.code ? { code: error.code } : {})
        });
        return;
      }

      if (error instanceof BadRequestError) {
        logWarn(
          "request_failed",
          {
            requestId: response.locals.requestId as string | undefined,
            method: request.method,
            path: request.path,
            statusCode: 400,
            errorName: error.name
          },
          { scope: "http", error }
        );
        response.status(400).json({
          error: error.message,
          ...(error.code ? { code: error.code } : {})
        });
        return;
      }

      if (error instanceof FeatureNotReadyError) {
        logWarn(
          "request_failed",
          {
            requestId: response.locals.requestId as string | undefined,
            method: request.method,
            path: request.path,
            statusCode: 501,
            errorName: error.name
          },
          { scope: "http", error }
        );
        response.status(501).json({
          error: error.message,
          ...(error.code ? { code: error.code } : {})
        });
        return;
      }

      if (error instanceof ZodError) {
        logWarn(
          "request_failed",
          {
            requestId: response.locals.requestId as string | undefined,
            method: request.method,
            path: request.path,
            statusCode: 400,
            errorName: error.name
          },
          { scope: "http", error }
        );
        response.status(400).json({ error: formatZodErrorMessage(error) });
        return;
      }

      if (error instanceof InvalidSessionStateError) {
        logWarn(
          "request_failed",
          {
            requestId: response.locals.requestId as string | undefined,
            method: request.method,
            path: request.path,
            statusCode: 409,
            errorName: error.name
          },
          { scope: "http", error }
        );
        response.status(409).json({ error: error.message });
        return;
      }

      if (error instanceof MalformedAIOutputError) {
        logWarn(
          "request_failed",
          {
            requestId: response.locals.requestId as string | undefined,
            method: request.method,
            path: request.path,
            statusCode: 422,
            errorName: error.name
          },
          { scope: "http", error }
        );
        response.status(422).json({ error: error.message });
        return;
      }

      if (error instanceof AIProviderError) {
        logError(
          "request_failed",
          {
            requestId: response.locals.requestId as string | undefined,
            method: request.method,
            path: request.path,
            statusCode: 502,
            errorName: error.name
          },
          { scope: "http", error }
        );
        const doctorSafeMessage = request.path.endsWith("/report-context/suggestions")
          ? "Could not generate note updates right now. Please try again."
          : error.message;
        response.status(502).json({ error: doctorSafeMessage });
        return;
      }

      if (isAbortError(error)) {
        logWarn(
          "request_aborted",
          {
            requestId: response.locals.requestId as string | undefined,
            path: request.path,
            method: request.method
          },
          {
            scope: "transcription",
            error
          }
        );
        response.status(499).json({
          error: "Request canceled before transcription completed."
        });
        return;
      }

      if (
        error instanceof Error &&
        "type" in error &&
        error.type === "entity.too.large"
      ) {
        logWarn(
          "request_failed",
          {
            requestId: response.locals.requestId as string | undefined,
            method: request.method,
            path: request.path,
            statusCode: 413,
            errorName: error.name
          },
          { scope: "http", error }
        );
        response.status(413).json({
          error: "Audio uploads must be 50 MB or smaller."
        });
        return;
      }

      logError(
        "request_failed",
        {
          requestId: response.locals.requestId as string | undefined,
          method: request.method,
          path: request.path,
          statusCode: 500
        },
        { scope: "http", error }
      );
      response.status(500).json({ error: "Internal server error" });
    }
  );

  return app;
}

function isAbortError(error: unknown): boolean {
  return error instanceof DOMException && error.name === "AbortError";
}
