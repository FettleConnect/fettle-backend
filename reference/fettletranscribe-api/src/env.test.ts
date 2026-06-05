import { describe, expect, it } from "vitest";

import { DEVELOPMENT_JWT_SECRET, parseEnv } from "./env.js";

describe("parseEnv", () => {
  it("rejects the default jwt secret in production", () => {
    expect(() =>
      parseEnv({
        NODE_ENV: "production",
        JWT_SECRET: DEVELOPMENT_JWT_SECRET,
        MONGODB_URI: "mongodb://127.0.0.1:27017/medivoice_rewrite",
        WEB_ORIGIN: "http://localhost:3000"
      })
    ).toThrow("JWT_SECRET must be set to a non-default value in production.");
  });

  it("allows the default jwt secret outside production", () => {
    expect(
      parseEnv({
        NODE_ENV: "development",
        JWT_SECRET: DEVELOPMENT_JWT_SECRET,
        MONGODB_URI: "mongodb://127.0.0.1:27017/medivoice_rewrite",
        WEB_ORIGIN: "http://localhost:3000"
      }).JWT_SECRET
    ).toBe(DEVELOPMENT_JWT_SECRET);
  });

  it("requires S3 and Twilio WhatsApp settings when PDF delivery is enabled", () => {
    expect(() =>
      parseEnv({
        NODE_ENV: "development",
        PDF_DELIVERY_ENABLED: "true",
        JWT_SECRET: DEVELOPMENT_JWT_SECRET,
        MONGODB_URI: "mongodb://127.0.0.1:27017/medivoice_rewrite",
        WEB_ORIGIN: "http://localhost:3000"
      })
    ).toThrow(
      "TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, TWILIO_WHATSAPP_FROM, PUBLIC_BASE_URL, AWS_REGION, and AWS_S3_BUCKET are required when PDF_DELIVERY_ENABLED is true."
    );
  });

  it("requires at least one internal API key when the internal API is enabled", () => {
    expect(() =>
      parseEnv({
        NODE_ENV: "development",
        INTERNAL_API_ENABLED: "true",
        JWT_SECRET: DEVELOPMENT_JWT_SECRET,
        MONGODB_URI: "mongodb://127.0.0.1:27017/medivoice_rewrite",
        WEB_ORIGIN: "http://localhost:3000"
      })
    ).toThrow(
      "INTERNAL_API_KEYS must include at least one shared secret when INTERNAL_API_ENABLED is true."
    );
  });

  it("parses comma-separated internal API keys", () => {
    const env = parseEnv({
      NODE_ENV: "development",
      INTERNAL_API_ENABLED: "true",
      INTERNAL_API_KEYS: "alpha , beta,gamma",
      JWT_SECRET: DEVELOPMENT_JWT_SECRET,
      MONGODB_URI: "mongodb://127.0.0.1:27017/medivoice_rewrite",
      WEB_ORIGIN: "http://localhost:3000"
    });

    expect(env.INTERNAL_API_KEYS).toEqual(["alpha", "beta", "gamma"]);
  });

  it("defaults the signed url ttl for pdf delivery", () => {
    const env = parseEnv({
      NODE_ENV: "development",
      JWT_SECRET: DEVELOPMENT_JWT_SECRET,
      MONGODB_URI: "mongodb://127.0.0.1:27017/medivoice_rewrite",
      WEB_ORIGIN: "http://localhost:3000"
    });

    expect(env.AWS_S3_SIGNED_URL_TTL_SECONDS).toBe(900);
    expect(env.TRANSCRIPTION_TIMEOUT_MS).toBe(180000);
    expect(env.TRANSCRIPTION_MAX_RETRIES).toBe(2);
    expect(env.TRANSCRIPTION_RECOVERY_INTERVAL_MS).toBe(60000);
  });

  it("requires a Gemini key when Gemini is explicitly selected", () => {
    expect(() =>
      parseEnv({
        NODE_ENV: "development",
        AI_PROVIDER: "gemini",
        JWT_SECRET: DEVELOPMENT_JWT_SECRET,
        MONGODB_URI: "mongodb://127.0.0.1:27017/medivoice_rewrite",
        WEB_ORIGIN: "http://localhost:3000"
      })
    ).toThrow("GEMINI_API_KEY is required when AI_PROVIDER is gemini.");
  });

  it("requires an OpenAI key when OpenAI is explicitly selected", () => {
    expect(() =>
      parseEnv({
        NODE_ENV: "development",
        AI_PROVIDER: "openai",
        JWT_SECRET: DEVELOPMENT_JWT_SECRET,
        MONGODB_URI: "mongodb://127.0.0.1:27017/medivoice_rewrite",
        WEB_ORIGIN: "http://localhost:3000"
      })
    ).toThrow("OPENAI_API_KEY is required when AI_PROVIDER is openai.");
  });

  it("requires an OpenAI key when OpenAI fallback is enabled", () => {
    expect(() =>
      parseEnv({
        NODE_ENV: "development",
        OPENAI_FALLBACK_ENABLED: "true",
        JWT_SECRET: DEVELOPMENT_JWT_SECRET,
        MONGODB_URI: "mongodb://127.0.0.1:27017/medivoice_rewrite",
        WEB_ORIGIN: "http://localhost:3000"
      })
    ).toThrow(
      "OPENAI_API_KEY is required when OPENAI_FALLBACK_ENABLED is true."
    );
  });
});
