import { z } from "zod";

export const DEVELOPMENT_JWT_SECRET = "development-only-jwt-secret";

const envBooleanSchema = z.preprocess((value) => {
  if (typeof value === "string") {
    const normalized = value.trim().toLowerCase();

    if (normalized === "true") {
      return true;
    }

    if (normalized === "false" || normalized === "") {
      return false;
    }
  }

  return value;
}, z.boolean());

const envStringListSchema = z.preprocess((value) => {
  if (typeof value === "string") {
    return value
      .split(",")
      .map((item) => item.trim())
      .filter((item) => item.length > 0);
  }

  return value;
}, z.array(z.string().trim().min(1)));

const envSchema = z.object({
  NODE_ENV: z
    .enum(["development", "test", "production"])
    .default("development"),
  PORT: z.coerce.number().int().min(1).max(65535).default(3001),
  WEB_ORIGIN: z.string().trim().url().default("http://localhost:3000"),
  INTERNAL_API_ENABLED: envBooleanSchema.default(false),
  INTERNAL_API_KEYS: envStringListSchema.default([]),
  PDF_DELIVERY_ENABLED: envBooleanSchema.default(false),
  PDF_DELIVERY_POLL_INTERVAL_MS: z.coerce
    .number()
    .int()
    .min(1000)
    .default(5000),
  JWT_SECRET: z.string().trim().min(1).default(DEVELOPMENT_JWT_SECRET),
  TWILIO_ACCOUNT_SID: z.string().trim().min(1).optional(),
  TWILIO_AUTH_TOKEN: z.string().trim().min(1).optional(),
  TWILIO_WHATSAPP_FROM: z.string().trim().min(1).optional(),
  TWILIO_WHATSAPP_CONTENT_SID: z.string().trim().min(1).optional(),
  TWILIO_WHATSAPP_CONTENT_VARIABLES: z.string().trim().min(1).optional(),
  PUBLIC_BASE_URL: z.string().trim().url().optional(),
  AWS_REGION: z.string().trim().min(1).optional(),
  AWS_S3_BUCKET: z.string().trim().min(1).optional(),
  AWS_S3_SIGNED_URL_TTL_SECONDS: z.coerce
    .number()
    .int()
    .min(60)
    .max(7 * 24 * 60 * 60)
    .default(900),
  MONGODB_URI: z
    .string()
    .trim()
    .min(1)
    .refine((uri) => /^mongodb(\+srv)?:\/\//.test(uri), {
      message: "MONGODB_URI must start with mongodb:// or mongodb+srv://"
    })
    .default("mongodb://127.0.0.1:27017/medivoice_rewrite"),
  AI_PROVIDER: z.enum(["gemini", "openai"]).optional(),
  GEMINI_API_KEY: z.string().trim().min(1).optional(),
  GEMINI_MODEL: z.string().trim().min(1).default("gemini-2.5-flash"),
  GEMINI_BASE_URL: z.string().trim().url().optional(),
  OPENAI_FALLBACK_ENABLED: envBooleanSchema.default(false),
  OPENAI_API_KEY: z.string().trim().min(1).optional(),
  OPENAI_TRANSCRIBE_MODEL: z
    .string()
    .trim()
    .min(1)
    .default("gpt-4o-transcribe-diarize"),
  OPENAI_SYNTHESIS_MODEL: z.string().trim().min(1).default("gpt-5.4-mini"),
  OPENAI_BASE_URL: z.string().trim().url().default("https://api.openai.com/v1"),
  TRANSCRIPTION_TIMEOUT_MS: z.coerce.number().int().min(1000).default(180_000),
  TRANSCRIPTION_MAX_RETRIES: z.coerce.number().int().min(0).max(5).default(2),
  TRANSCRIPTION_RETRY_DELAY_MS: z.coerce.number().int().min(100).default(1000),
  TRANSCRIPTION_CIRCUIT_MAX_FAILURES: z.coerce.number().int().min(1).max(20).default(3),
  TRANSCRIPTION_CIRCUIT_COOLDOWN_MS: z.coerce.number().int().min(1000).default(30_000),
  TRANSCRIPTION_RECOVERY_INTERVAL_MS: z.coerce.number().int().min(5000).default(60_000),
  TRANSCRIPTION_RECOVERY_STALE_AFTER_MS: z.coerce.number().int().min(10_000).default(10 * 60 * 1000),
  REPORT_CONTEXT_TIMEOUT_MS: z.coerce.number().int().min(5000).max(120_000).default(30_000),
  REPORT_CONTEXT_MAX_RETRIES: z.coerce.number().int().min(0).max(3).default(1),
  REPORT_CONTEXT_RETRY_DELAY_MS: z.coerce.number().int().min(100).max(30_000).default(750),
  REPORT_CONTEXT_SUGGEST_SCHEMA_RETRIES: z.coerce.number().int().min(0).max(3).default(1),
  REPORT_CONTEXT_CONCURRENCY: z.coerce.number().int().min(1).max(4).default(2),
  REPORT_CONTEXT_MAX_UNITS: z.coerce.number().int().min(1).max(300).default(100),
  REPORT_CONTEXT_RECOVERY_INTERVAL_MS: z.coerce
    .number()
    .int()
    .min(5000)
    .default(60_000),
  REPORT_CONTEXT_RECOVERY_STALE_AFTER_MS: z.coerce
    .number()
    .int()
    .min(10_000)
    .default(10 * 60 * 1000),
  KPI_RETENTION_DAYS: z.coerce.number().int().min(1).max(365).default(90),
  KPI_WARMUP_DAYS: z.coerce.number().int().min(1).max(365).default(30),
  KPI_MIN_SAMPLE_SIZE: z.coerce.number().int().min(1).max(1000).default(1),
  FETTLE_SYNC_ENABLED: envBooleanSchema.default(false),
  FETTLE_BACKEND_URL: z.string().trim().url().optional(),
  FETTLE_SYNC_SECRET: z.string().trim().min(1).optional(),
  FETTLE_SYNC_WORKER_INTERVAL_MS: z.coerce.number().int().min(1000).default(15_000),
  FETTLE_SYNC_TIMEOUT_MS: z.coerce.number().int().min(1000).max(120_000).default(8_000),
  FETTLE_SYNC_MAX_ATTEMPTS: z.coerce.number().int().min(1).max(100).default(10),
  FETTLE_SYNC_STALE_AFTER_MS: z.coerce.number().int().min(1000).default(5 * 60_000),
  FETTLE_SYNC_RETRY_WINDOW_MS: z.coerce
    .number()
    .int()
    .min(60_000)
    .default(24 * 60 * 60 * 1000),
  FETTLE_SYNC_RETRY_DELAY_MS: z.coerce.number().int().min(100).default(15_000),
  FETTLE_SYNC_BATCH_SIZE: z.coerce.number().int().min(1).max(100).default(10)
});

export type AppEnv = z.infer<typeof envSchema>;

export function parseEnv(source: NodeJS.ProcessEnv = process.env): AppEnv {
  const env = envSchema.parse(source);

  if (
    env.NODE_ENV === "production" &&
    env.JWT_SECRET === DEVELOPMENT_JWT_SECRET
  ) {
    throw new Error(
      "JWT_SECRET must be set to a non-default value in production."
    );
  }

  if (env.INTERNAL_API_ENABLED && env.INTERNAL_API_KEYS.length === 0) {
    throw new Error(
      "INTERNAL_API_KEYS must include at least one shared secret when INTERNAL_API_ENABLED is true."
    );
  }

  if (
    env.PDF_DELIVERY_ENABLED &&
    (!env.TWILIO_ACCOUNT_SID ||
      !env.TWILIO_AUTH_TOKEN ||
      !env.TWILIO_WHATSAPP_FROM ||
      !env.PUBLIC_BASE_URL ||
      !env.AWS_REGION ||
      !env.AWS_S3_BUCKET)
  ) {
    throw new Error(
      "TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, TWILIO_WHATSAPP_FROM, PUBLIC_BASE_URL, AWS_REGION, and AWS_S3_BUCKET are required when PDF_DELIVERY_ENABLED is true."
    );
  }

  if (env.AI_PROVIDER === "gemini" && !env.GEMINI_API_KEY) {
    throw new Error("GEMINI_API_KEY is required when AI_PROVIDER is gemini.");
  }

  if (env.AI_PROVIDER === "openai" && !env.OPENAI_API_KEY) {
    throw new Error("OPENAI_API_KEY is required when AI_PROVIDER is openai.");
  }

  if (env.OPENAI_FALLBACK_ENABLED && !env.OPENAI_API_KEY) {
    throw new Error(
      "OPENAI_API_KEY is required when OPENAI_FALLBACK_ENABLED is true."
    );
  }

  if (env.FETTLE_SYNC_ENABLED && (!env.FETTLE_BACKEND_URL || !env.FETTLE_SYNC_SECRET)) {
    throw new Error(
      "FETTLE_BACKEND_URL and FETTLE_SYNC_SECRET are required when FETTLE_SYNC_ENABLED is true."
    );
  }

  return env;
}
