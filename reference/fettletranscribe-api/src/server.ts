import { type Server } from "node:http";

import { createApp } from "./app.js";
import { AuthService } from "./auth-service.js";
import { MongoDoctorRepository } from "./doctor-repository.js";
import { parseEnv } from "./env.js";
import { isInternalApiEnabled } from "./internal-api.js";
import { PRELOADED_DOCTORS } from "./preloaded-doctors.js";
import {
  closeAllMongoClients,
  getMongoClient,
  resolveMongoDatabaseName
} from "./mongo.js";
import { S3PdfAssetStorage } from "./pdf-asset-storage.js";
import { MongoPdfDeliveryRepository } from "./pdf-delivery-repository.js";
import { PdfDeliveryService } from "./pdf-delivery-service.js";
import { MongoQueueRepository } from "./queue-repository.js";
import { QueueService } from "./queue-service.js";
import {
  FallbackReportContextAdapter,
  GeminiReportContextAdapter,
  MockReportContextAdapter,
  OpenAIReportContextAdapter,
  type ReportContextAdapter
} from "./report-context-service.js";
import { MongoSessionRepository } from "./session-repository.js";
import { SessionService } from "./session-service.js";
import { DefaultTwilioRequestValidator } from "./twilio-request-validator.js";
import { emitKpi } from "./kpi.js";
import {
  FettleSyncService,
  MongoFettleSyncRepository
} from "./fettle-sync-service.js";
import { MongoKpiEventStore } from "./kpi-event-store.js";
import {
  configureKpiMinimumSampleSize,
  configureKpiEventStore,
  warmKpiSummaryFromStore
} from "./kpi-registry.js";
import {
  FallbackTranscriptionAdapter,
  GeminiTranscriptionAdapter,
  MockGeminiTranscriptionAdapter,
  OpenAITranscriptionAdapter,
  type TranscriptionAdapter
} from "./transcription-adapter.js";
import { ReliableTranscriptionAdapter } from "./reliable-transcription-adapter.js";
import { TwilioWhatsappSender } from "./whatsapp-delivery.js";
import { logError, logInfo, logWarn } from "./logger.js";

const env = parseEnv();
const internalApi = {
  enabled: env.INTERNAL_API_ENABLED,
  apiKeys: env.INTERNAL_API_KEYS
};

async function bootstrap() {
  configureKpiMinimumSampleSize(env.KPI_MIN_SAMPLE_SIZE);
  const client = await getMongoClient(env.MONGODB_URI);
  const dbName = resolveMongoDatabaseName(env.MONGODB_URI);
  const repository = new MongoSessionRepository(client, dbName);
  const queueRepository = new MongoQueueRepository(client, dbName);
  const doctorRepository = new MongoDoctorRepository(client, dbName);
  const fettleSyncRepository = new MongoFettleSyncRepository(client, dbName);
  const kpiEventStore = new MongoKpiEventStore(
    client,
    env.MONGODB_URI,
    env.KPI_RETENTION_DAYS
  );
  const pdfDeliveryRepository = env.PDF_DELIVERY_ENABLED
    ? new MongoPdfDeliveryRepository(client, dbName)
    : null;
  await repository.ensureIndexes();
  await queueRepository.ensureIndexes();
  await doctorRepository.ensureIndexes();
  await fettleSyncRepository.ensureIndexes();
  let warmedKpiEvents = 0;
  try {
    await kpiEventStore.ensureIndexes();
    configureKpiEventStore({
      store: kpiEventStore,
      onError: (error) => {
        logError(
          "kpi_event_persistence_failed",
          {},
          { scope: "kpi", error }
        );
      }
    });
    const warmupSince = new Date(
      Date.now() - env.KPI_WARMUP_DAYS * 24 * 60 * 60 * 1000
    ).toISOString();
    warmedKpiEvents = await warmKpiSummaryFromStore(warmupSince);
  } catch (error) {
    configureKpiEventStore({ store: null });
    logWarn(
      "kpi_persistence_disabled_after_bootstrap_error",
      {
        reason:
          error instanceof Error ? error.message : "unknown_kpi_bootstrap_error"
      },
      { scope: "kpi" }
    );
  }
  const seededDoctors = await doctorRepository.seedDoctors(PRELOADED_DOCTORS);
  if (pdfDeliveryRepository) {
    await pdfDeliveryRepository.ensureIndexes();
  }
  const cleanedLegacyTranscriptions =
    await repository.cleanupLegacyNullTranscriptionFields();
  const recoveredTranscriptions =
    await repository.recoverInterruptedTranscriptions();
  const configuredProvider =
    env.AI_PROVIDER ?? (env.GEMINI_API_KEY ? "gemini" : env.OPENAI_API_KEY ? "openai" : undefined);
  const useMockTranscription = !configuredProvider;
  const whatsappContentVariables = env.TWILIO_WHATSAPP_CONTENT_VARIABLES
    ? parseWhatsappContentVariables(env.TWILIO_WHATSAPP_CONTENT_VARIABLES)
    : undefined;
  const transcriptionAdapter = createTranscriptionAdapter();
  const reportContextAdapter = createReportContextAdapter();
  const pdfDeliveryService = env.PDF_DELIVERY_ENABLED
    ? new PdfDeliveryService(
        pdfDeliveryRepository!,
        repository,
        new S3PdfAssetStorage({
          region: env.AWS_REGION!,
          bucketName: env.AWS_S3_BUCKET!,
          signedUrlTtlSeconds: env.AWS_S3_SIGNED_URL_TTL_SECONDS
        }),
        new TwilioWhatsappSender({
          accountSid: env.TWILIO_ACCOUNT_SID!,
          authToken: env.TWILIO_AUTH_TOKEN!,
          fromWhatsAppNumber: env.TWILIO_WHATSAPP_FROM!,
          statusCallbackUrl: `${env.PUBLIC_BASE_URL!.replace(/\/$/, "")}/api/webhooks/twilio/whatsapp-status`,
          ...(env.TWILIO_WHATSAPP_CONTENT_SID
            ? { contentSid: env.TWILIO_WHATSAPP_CONTENT_SID }
            : {}),
          ...(whatsappContentVariables
            ? {
                contentVariables: whatsappContentVariables
              }
            : {})
        }),
        {
          baseUrl: `${env.PUBLIC_BASE_URL!.replace(/\/$/, "")}/api`,
          signingSecret: env.JWT_SECRET,
          tokenTtlSeconds: env.AWS_S3_SIGNED_URL_TTL_SECONDS
        }
      )
    : undefined;
  const fettleSyncService = new FettleSyncService(fettleSyncRepository, {
    enabled: env.FETTLE_SYNC_ENABLED,
    ...(env.FETTLE_BACKEND_URL ? { backendUrl: env.FETTLE_BACKEND_URL } : {}),
    ...(env.FETTLE_SYNC_SECRET ? { secret: env.FETTLE_SYNC_SECRET } : {}),
    timeoutMs: env.FETTLE_SYNC_TIMEOUT_MS,
    maxAttempts: env.FETTLE_SYNC_MAX_ATTEMPTS,
    retryWindowMs: env.FETTLE_SYNC_RETRY_WINDOW_MS,
    baseRetryDelayMs: env.FETTLE_SYNC_RETRY_DELAY_MS,
    staleProcessingMs: env.FETTLE_SYNC_STALE_AFTER_MS
  });
  const sessionService = new SessionService(
    repository,
    transcriptionAdapter,
    undefined,
    pdfDeliveryService,
    reportContextAdapter,
    fettleSyncService
  );
  const queueService = new QueueService(queueRepository, sessionService);
  const authService = new AuthService(doctorRepository, env.JWT_SECRET);
  const app = createApp({
    sessionService,
    queueService,
    authService,
    webOrigin: env.WEB_ORIGIN,
    useMockTranscription,
    internalApi,
    ...(pdfDeliveryService ? { pdfDeliveryService } : {}),
    fettleSyncService,
    ...(env.PDF_DELIVERY_ENABLED && env.TWILIO_AUTH_TOKEN && env.PUBLIC_BASE_URL
      ? {
          twilioRequestValidator: new DefaultTwilioRequestValidator(
            env.TWILIO_AUTH_TOKEN
          ),
          twilioWebhookUrl: `${env.PUBLIC_BASE_URL.replace(/\/$/, "")}/api/webhooks/twilio/whatsapp-status`
        }
      : {})
  });
  const pdfDeliveryInterval = env.PDF_DELIVERY_ENABLED
    && pdfDeliveryService
    ? setInterval(() => {
        void pdfDeliveryService.processPendingJobs(10).catch((error) => {
          logError(
            "pdf_delivery_worker_batch_failed",
            {},
            {
              scope: "server",
              error
            }
          );
        });
      }, env.PDF_DELIVERY_POLL_INTERVAL_MS)
    : null;
  const fettleSyncInterval = env.FETTLE_SYNC_ENABLED
    ? setInterval(() => {
        void fettleSyncService
          .processDueJobs(env.FETTLE_SYNC_BATCH_SIZE)
          .catch((error) => {
            logError(
              "fettle_sync_worker_batch_failed",
              {},
              { scope: "fettle_sync", error }
            );
          });
      }, env.FETTLE_SYNC_WORKER_INTERVAL_MS)
    : null;
  const transcriptionRecoveryInterval = setInterval(() => {
    void repository
      .recoverStaleProcessingTranscriptions(
        env.TRANSCRIPTION_RECOVERY_STALE_AFTER_MS
      )
      .then((recoveredCount) => {
        emitKpi(
          "session.transcription.stale_recovery",
          "success",
          { recoveredCount },
          { scope: "server" }
        );
        if (recoveredCount > 0) {
          logWarn(
            "transcription_stale_recovery_performed",
            { recoveredCount },
            { scope: "server" }
          );
        }
      })
      .catch((error) => {
        emitKpi(
          "session.transcription.stale_recovery",
          "failure",
          { error: error instanceof Error ? error.message : "unknown" },
          { scope: "server" }
        );
        logError(
          "transcription_stale_recovery_failed",
          {},
          {
            scope: "server",
            error
          }
        );
      });
  }, env.TRANSCRIPTION_RECOVERY_INTERVAL_MS);
  const reportContextRecoveryInterval = setInterval(() => {
    void sessionService
      .recoverStaleReportContextExtractionJobs(
        env.REPORT_CONTEXT_RECOVERY_STALE_AFTER_MS
      )
      .then((recoveredCount) => {
        emitKpi(
          "report_context.job.stale_recovery",
          "success",
          { recoveredCount },
          { scope: "server" }
        );
        if (recoveredCount > 0) {
          logWarn(
            "report_context_stale_recovery_performed",
            { recoveredCount },
            { scope: "server" }
          );
        }
      })
      .catch((error) => {
        emitKpi(
          "report_context.job.stale_recovery",
          "failure",
          { error: error instanceof Error ? error.message : "unknown" },
          { scope: "server" }
        );
        logError(
          "report_context_stale_recovery_failed",
          {},
          { scope: "server", error }
        );
      });
  }, env.REPORT_CONTEXT_RECOVERY_INTERVAL_MS);

  if (env.NODE_ENV !== "test") {
    const server = app.listen(env.PORT, () => {
      logInfo(
        "server_started",
        {
          port: env.PORT,
          nodeEnv: env.NODE_ENV,
          mockTranscription: useMockTranscription,
          internalApiEnabled: isInternalApiEnabled(internalApi),
          fettleSyncEnabled: env.FETTLE_SYNC_ENABLED,
          warmedKpiEvents
        },
        { scope: "server" }
      );
      if (useMockTranscription) {
        logWarn(
          "mock_transcription_enabled",
          {
            reason: "No GEMINI_API_KEY set"
          },
          { scope: "server" }
        );
      }
      if (recoveredTranscriptions > 0) {
        logWarn(
          "transcription_recovery_performed",
          {
            recoveredCount: recoveredTranscriptions
          },
          { scope: "server" }
        );
      }
      if (cleanedLegacyTranscriptions > 0) {
        logWarn(
          "legacy_transcription_cleanup_performed",
          {
            cleanedCount: cleanedLegacyTranscriptions
          },
          { scope: "server" }
        );
      }
      if (seededDoctors > 0) {
        logInfo(
          "doctor_seed_completed",
          {
            insertedCount: seededDoctors
          },
          { scope: "server" }
        );
      }
    });
    registerShutdownHandlers(server, [
      pdfDeliveryInterval,
      fettleSyncInterval,
      transcriptionRecoveryInterval,
      reportContextRecoveryInterval
    ]);
  }
}

function createTranscriptionAdapter(): TranscriptionAdapter {
  const configuredProvider =
    env.AI_PROVIDER ?? (env.GEMINI_API_KEY ? "gemini" : env.OPENAI_API_KEY ? "openai" : undefined);

  const reliabilityPolicy = {
    timeoutMs: env.TRANSCRIPTION_TIMEOUT_MS,
    // Keep whole-operation retries off so provider-level retry/fallback policy stays deterministic.
    maxRetries: 0,
    retryDelayMs: env.TRANSCRIPTION_RETRY_DELAY_MS,
    maxFailuresBeforeOpen: env.TRANSCRIPTION_CIRCUIT_MAX_FAILURES,
    cooldownMs: env.TRANSCRIPTION_CIRCUIT_COOLDOWN_MS
  } as const;

  if (!configuredProvider) {
    return new ReliableTranscriptionAdapter(
      new MockGeminiTranscriptionAdapter(),
      reliabilityPolicy
    );
  }

  if (configuredProvider === "openai") {
    return new ReliableTranscriptionAdapter(
      createOpenAITranscriptionAdapter(),
      reliabilityPolicy
    );
  }

  const geminiAdapter = new GeminiTranscriptionAdapter({
    apiKey: env.GEMINI_API_KEY!,
    model: env.GEMINI_MODEL,
    ...(env.GEMINI_BASE_URL ? { baseUrl: env.GEMINI_BASE_URL } : {})
  });

  if (env.OPENAI_FALLBACK_ENABLED && env.OPENAI_API_KEY) {
    return new ReliableTranscriptionAdapter(
      new FallbackTranscriptionAdapter({
        primary: geminiAdapter,
        fallback: createOpenAITranscriptionAdapter()
      }),
      reliabilityPolicy
    );
  }

  return new ReliableTranscriptionAdapter(geminiAdapter, reliabilityPolicy);
}

function createOpenAITranscriptionAdapter(): OpenAITranscriptionAdapter {
  return new OpenAITranscriptionAdapter({
    apiKey: env.OPENAI_API_KEY!,
    transcribeModel: env.OPENAI_TRANSCRIBE_MODEL,
    synthesisModel: env.OPENAI_SYNTHESIS_MODEL,
    baseUrl: env.OPENAI_BASE_URL
  });
}

function createReportContextAdapter(): ReportContextAdapter {
  const openAIAdapter = env.OPENAI_API_KEY
    ? new OpenAIReportContextAdapter({
        apiKey: env.OPENAI_API_KEY,
        model: env.OPENAI_SYNTHESIS_MODEL,
        baseUrl: env.OPENAI_BASE_URL,
        timeoutMs: env.REPORT_CONTEXT_TIMEOUT_MS,
        maxRetries: env.REPORT_CONTEXT_MAX_RETRIES,
        retryDelayMs: env.REPORT_CONTEXT_RETRY_DELAY_MS,
        suggestSchemaRetries: env.REPORT_CONTEXT_SUGGEST_SCHEMA_RETRIES,
        concurrency: env.REPORT_CONTEXT_CONCURRENCY,
        maxUnits: env.REPORT_CONTEXT_MAX_UNITS
      })
    : null;

  if (!env.GEMINI_API_KEY) {
    if (openAIAdapter) {
      logWarn(
        "report_context_primary_switched_to_openai",
        { reason: "GEMINI_API_KEY_missing" },
        { scope: "server" }
      );
      return openAIAdapter;
    }
    return new MockReportContextAdapter();
  }

  const geminiAdapter = new GeminiReportContextAdapter({
    apiKey: env.GEMINI_API_KEY,
    model: env.GEMINI_MODEL,
    timeoutMs: env.REPORT_CONTEXT_TIMEOUT_MS,
    maxRetries: env.REPORT_CONTEXT_MAX_RETRIES,
    retryDelayMs: env.REPORT_CONTEXT_RETRY_DELAY_MS,
    suggestSchemaRetries: env.REPORT_CONTEXT_SUGGEST_SCHEMA_RETRIES,
    concurrency: env.REPORT_CONTEXT_CONCURRENCY,
    maxUnits: env.REPORT_CONTEXT_MAX_UNITS,
    ...(env.GEMINI_BASE_URL ? { baseUrl: env.GEMINI_BASE_URL } : {})
  });

  if (env.OPENAI_FALLBACK_ENABLED && openAIAdapter) {
    return new FallbackReportContextAdapter({
      primary: geminiAdapter,
      fallback: openAIAdapter
    });
  }

  return geminiAdapter;
}

function closeServer(server: Server): Promise<void> {
  return new Promise((resolve, reject) => {
    server.close((error) => {
      if (error) {
        reject(error);
        return;
      }

      resolve();
    });
  });
}

function registerShutdownHandlers(
  server: Server,
  intervals: Array<NodeJS.Timeout | null>
) {
  const shutdown = async (signal: string) => {
    try {
      for (const interval of intervals) {
        if (interval) {
          clearInterval(interval);
        }
      }
      await closeServer(server);
      await closeAllMongoClients();
      logInfo(
        "server_shutdown_completed",
        {
          signal
        },
        { scope: "server" }
      );
      process.exit(0);
    } catch (error) {
      logError(
        "server_shutdown_failed",
        {
          signal
        },
        {
          scope: "server",
          error
        }
      );
      process.exit(1);
    }
  };

  process.once("SIGINT", () => {
    void shutdown("SIGINT");
  });

  process.once("SIGTERM", () => {
    void shutdown("SIGTERM");
  });
}

bootstrap().catch((error) => {
  logError("server_start_failed", {}, { scope: "server", error });
  process.exit(1);
});

function parseWhatsappContentVariables(
  raw: string
): Record<string, string> | undefined {
  try {
    const parsed = JSON.parse(raw) as unknown;

    if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
      throw new Error("Expected a JSON object");
    }

    const entries = Object.entries(parsed);
    const variables: Record<string, string> = {};

    for (const [key, value] of entries) {
      if (typeof value !== "string") {
        throw new Error("All content variable values must be strings");
      }
      variables[key] = value;
    }

    return variables;
  } catch (error) {
    logWarn(
      "twilio_whatsapp_content_variables_invalid",
      {
        reason:
          error instanceof Error
            ? error.message
            : "Invalid TWILIO_WHATSAPP_CONTENT_VARIABLES"
      },
      { scope: "server" }
    );
    return undefined;
  }
}
