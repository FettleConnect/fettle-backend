import { createServer, type Server } from "node:http";

import { appVersion } from "@medivoice/shared";
import { afterEach, describe, expect, it, vi } from "vitest";
import { z } from "zod";

import { createApp, createHealthPayload, formatZodErrorMessage } from "./app.js";
import { AuthService } from "./auth-service.js";
import { InMemoryDoctorRepository } from "./doctor-repository.js";
import { issueDoctorToken } from "./auth-token.js";
import { InMemoryPdfDeliveryRepository } from "./pdf-delivery-repository.js";
import { PdfDeliveryService } from "./pdf-delivery-service.js";
import { InMemoryQueueRepository } from "./queue-repository.js";
import { QueueService } from "./queue-service.js";
import {
  FettleSyncService,
  InMemoryFettleSyncRepository
} from "./fettle-sync-service.js";
import { InMemorySessionRepository } from "./session-repository.js";
import { SessionService } from "./session-service.js";
import { emitKpi } from "./kpi.js";
import { resetKpiSummary } from "./kpi-registry.js";
import {
  MockGeminiTranscriptionAdapter,
  type TranscriptionAdapter,
  type TranscriptionAdapterInput,
  type TranscriptionAdapterResult
} from "./transcription-adapter.js";

const activeServers = new Set<Server>();
const testJwtSecret = "test-secret";
const testInternalApiKey = "internal-secret";

afterEach(async () => {
  resetKpiSummary();
  await Promise.all(
    [...activeServers].map(
      (server) =>
        new Promise<void>((resolve, reject) => {
          server.close((error) => {
            activeServers.delete(server);

            if (error) {
              reject(error);
              return;
            }

            resolve();
          });
        })
    )
  );
});

async function createAuthenticatedAuthService() {
  const doctorRepository = new InMemoryDoctorRepository();
  const authService = new AuthService(
    doctorRepository,
    testJwtSecret
  );
  const doctor = await doctorRepository.createOrUpdateDoctor({
    name: "Dr. Rao",
    phoneNumber: "9876543210"
  });
  const token = issueDoctorToken(doctor, testJwtSecret);

  return {
    authService,
    doctor,
    token
  };
}

describe("createHealthPayload", () => {
  it("returns the typed health payload used by the route", () => {
    const payload = createHealthPayload();

    expect(payload.status).toBe("ok");
    expect(payload.service).toBe("api");
    expect(payload.version).toBe(appVersion);
    expect(payload.timestamp).toContain("T");
  });
});

describe("formatZodErrorMessage", () => {
  it("returns patient-friendly field validation messages", () => {
    const result = z
      .object({
        patient: z.object({
          phoneNumber: z.string().min(10)
        })
      })
      .safeParse({
        patient: {
          phoneNumber: "892038432"
        }
      });

    if (result.success) {
      throw new Error("Expected validation to fail.");
    }

    expect(formatZodErrorMessage(result.error)).toBe(
      "Patient mobile number must be at least 10 digits."
    );
  });
});

describe("InMemoryQueueRepository", () => {
  it("matches queue state transition semantics more closely to Mongo", async () => {
    const repository = new InMemoryQueueRepository();
    const firstEntry = await repository.createQueueEntry({
      doctorId: "9f4b7f58-07e3-4338-a876-a75769a01855",
      intakeLinkId: "1c2108eb-d5bf-43e3-a747-649842cd32bb",
      intakeLabel: "Clinic Queue",
      patient: {
        name: "Anita Sharma",
        phoneNumber: "9876543210",
        age: 32
      },
      source: "qr"
    });

    const secondEntry = await repository.createQueueEntry({
      doctorId: "9f4b7f58-07e3-4338-a876-a75769a01855",
      intakeLinkId: "1c2108eb-d5bf-43e3-a747-649842cd32bb",
      intakeLabel: "Clinic Queue",
      patient: {
        name: "Anita Sharma",
        phoneNumber: "9876543210",
        age: 32
      },
      source: "qr"
    });

    expect(secondEntry.id).not.toBe(firstEntry.id);

    const acceptedEntry = await repository.acceptQueueEntry(
      firstEntry.id,
      firstEntry.id,
      new Date().toISOString()
    );

    expect(acceptedEntry?.status).toBe("accepted");

    const repeatedAccept = await repository.acceptQueueEntry(
      firstEntry.id,
      firstEntry.id,
      new Date().toISOString()
    );

    expect(repeatedAccept?.status).toBe("accepted");
    expect(repeatedAccept?.id).toBe(firstEntry.id);

    const rejectedStatusUpdate = await repository.updateQueueEntryStatus(
      firstEntry.id,
      "cancelled",
      new Date().toISOString()
    );

    expect(rejectedStatusUpdate).toBeNull();
  });

  it("refreshes the stored intake link doctor name when the doctor name changes", async () => {
    const repository = new InMemoryQueueRepository();

    const originalLink = await repository.getOrCreateDefaultIntakeLink({
      doctorId: "9f4b7f58-07e3-4338-a876-a75769a01855",
      doctorName: "DR. Prathyush Chitithoti"
    });

    const updatedLink = await repository.getOrCreateDefaultIntakeLink({
      doctorId: "9f4b7f58-07e3-4338-a876-a75769a01855",
      doctorName: "Dr. Prathyush Chitithoti"
    });

    expect(updatedLink.id).toBe(originalLink.id);
    expect(updatedLink.doctorName).toBe("Dr. Prathyush Chitithoti");
    expect(new Date(updatedLink.updatedAt).getTime()).toBeGreaterThanOrEqual(
      new Date(originalLink.updatedAt).getTime()
    );
  });
});

describe("createApp", () => {
  it("returns found=false for unknown patient profile phone lookup", async () => {
    const { authService, token } = await createAuthenticatedAuthService();
    const app = createApp({ authService, webOrigin: "http://localhost:3000" });
    const server = createServer(app);
    activeServers.add(server);
    await new Promise<void>((resolve) => {
      server.listen(0, "127.0.0.1", () => resolve());
    });
    const address = server.address();
    if (!address || typeof address === "string") {
      throw new Error("Expected the test server to bind to a TCP port.");
    }

    const response = await fetch(
      `http://127.0.0.1:${address.port}/patients/profile-by-phone?phone=9999999999`,
      { headers: { Authorization: `Bearer ${token}` } }
    );
    expect(response.status).toBe(200);
    const payload = (await response.json()) as { found: boolean };
    expect(payload.found).toBe(false);
  });

  it("returns found profile for known phone lookup", async () => {
    const { authService, token } = await createAuthenticatedAuthService();
    const app = createApp({ authService, webOrigin: "http://localhost:3000" });
    const server = createServer(app);
    activeServers.add(server);
    await new Promise<void>((resolve) => {
      server.listen(0, "127.0.0.1", () => resolve());
    });
    const address = server.address();
    if (!address || typeof address === "string") {
      throw new Error("Expected the test server to bind to a TCP port.");
    }

    await fetch(`http://127.0.0.1:${address.port}/sessions`, {
      method: "POST",
      headers: {
        Authorization: `Bearer ${token}`,
        "Content-Type": "application/json"
      },
      body: JSON.stringify({
        patient: { name: "Anita Sharma", phoneNumber: "9876543210", age: 32 }
      })
    });

    const response = await fetch(
      `http://127.0.0.1:${address.port}/patients/profile-by-phone?phone=9876543210`,
      { headers: { Authorization: `Bearer ${token}` } }
    );
    expect(response.status).toBe(200);
    const payload = (await response.json()) as {
      found: boolean;
      profile?: { name: string; age?: number };
    };
    expect(payload.found).toBe(true);
    expect(payload.profile?.name).toBe("Anita Sharma");
    expect(payload.profile?.age).toBe(32);
  });

  it("upserts patient profile with latest edited values on session create", async () => {
    const { authService, token } = await createAuthenticatedAuthService();
    const app = createApp({ authService, webOrigin: "http://localhost:3000" });
    const server = createServer(app);
    activeServers.add(server);
    await new Promise<void>((resolve) => {
      server.listen(0, "127.0.0.1", () => resolve());
    });
    const address = server.address();
    if (!address || typeof address === "string") {
      throw new Error("Expected the test server to bind to a TCP port.");
    }

    await fetch(`http://127.0.0.1:${address.port}/sessions`, {
      method: "POST",
      headers: {
        Authorization: `Bearer ${token}`,
        "Content-Type": "application/json"
      },
      body: JSON.stringify({
        patient: { name: "Anita Sharma", phoneNumber: "9876543210", age: 32 }
      })
    });
    await fetch(`http://127.0.0.1:${address.port}/sessions`, {
      method: "POST",
      headers: {
        Authorization: `Bearer ${token}`,
        "Content-Type": "application/json"
      },
      body: JSON.stringify({
        patient: { name: "Anita S", phoneNumber: "9876543210", age: 33 }
      })
    });

    const response = await fetch(
      `http://127.0.0.1:${address.port}/patients/profile-by-phone?phone=9876543210`,
      { headers: { Authorization: `Bearer ${token}` } }
    );
    expect(response.status).toBe(200);
    const payload = (await response.json()) as {
      found: boolean;
      profile?: { name: string; age?: number };
    };
    expect(payload.found).toBe(true);
    expect(payload.profile?.name).toBe("Anita S");
    expect(payload.profile?.age).toBe(33);
  });

  it("serves public KPI scorecard with per-section ratings", async () => {
    emitKpi("auth.pin_login", "success", { elapsedMs: 120 });
    emitKpi("auth.pin_login", "failure", { elapsedMs: 80 });
    emitKpi("session.audio_upload", "success", { elapsedMs: 300 });
    const app = createApp({ webOrigin: "http://localhost:3000" });
    const server = createServer(app);
    activeServers.add(server);

    await new Promise<void>((resolve) => {
      server.listen(0, "127.0.0.1", () => resolve());
    });

    const address = server.address();

    if (!address || typeof address === "string") {
      throw new Error("Expected the test server to bind to a TCP port.");
    }

    const response = await fetch(
      `http://127.0.0.1:${address.port}/public/kpi/scorecard`
    );
    expect(response.status).toBe(200);
    const payload = (await response.json()) as {
      generatedAt: string;
      sections: Array<{
        section: string;
        rating: string;
        metrics: Array<{ metric: string; rating: string }>;
      }>;
    };

    expect(payload.generatedAt).toContain("T");
    expect(payload.sections.length).toBeGreaterThan(0);
    expect(payload.sections.some((section) => section.section === "auth")).toBe(
      true
    );
    expect(
      payload.sections.flatMap((section) => section.metrics).some((metric) => (
        metric.metric === "auth.pin_login" && typeof metric.rating === "string"
      ))
    ).toBe(true);
  });

  it("supports focus mode ordering for KPI scorecard", async () => {
    emitKpi("auth.pin_login", "success", { elapsedMs: 100 });
    emitKpi("auth.pin_login", "failure", { elapsedMs: 100 });
    emitKpi("session.audio_upload", "success", { elapsedMs: 100 });
    emitKpi("session.audio_upload", "success", { elapsedMs: 100 });
    const app = createApp({ webOrigin: "http://localhost:3000" });
    const server = createServer(app);
    activeServers.add(server);

    await new Promise<void>((resolve) => {
      server.listen(0, "127.0.0.1", () => resolve());
    });

    const address = server.address();
    if (!address || typeof address === "string") {
      throw new Error("Expected the test server to bind to a TCP port.");
    }

    const response = await fetch(
      `http://127.0.0.1:${address.port}/public/kpi/scorecard?focus=1`
    );
    expect(response.status).toBe(200);
    const payload = (await response.json()) as {
      sections: Array<{ section: string; rating: string }>;
    };

    expect(payload.sections.length).toBeGreaterThan(1);
    expect(payload.sections[0]?.section).toBe("auth");
  });

  it("supports KPI scorecard filters and expected-metric visibility", async () => {
    emitKpi("auth.pin_login", "failure", { elapsedMs: 100 });
    const app = createApp({ webOrigin: "http://localhost:3000" });
    const server = createServer(app);
    activeServers.add(server);

    await new Promise<void>((resolve) => {
      server.listen(0, "127.0.0.1", () => resolve());
    });

    const address = server.address();
    if (!address || typeof address === "string") {
      throw new Error("Expected the test server to bind to a TCP port.");
    }

    const response = await fetch(
      `http://127.0.0.1:${address.port}/public/kpi/scorecard?includeExpected=1&section=session`
    );
    expect(response.status).toBe(200);
    const payload = (await response.json()) as {
      sections: Array<{
        section: string;
        metrics: Array<{ metric: string; total: number }>;
      }>;
    };

    expect(payload.sections.length).toBe(1);
    expect(payload.sections[0]?.section).toBe("session");
    expect(
      payload.sections[0]?.metrics.some(
        (metric) =>
          metric.metric === "session.transcription.end_to_end" &&
          metric.total === 0
      )
    ).toBe(true);
  });

  it("serves provider KPI diagnostics with latency percentiles", async () => {
    emitKpi("provider.gemini.health", "success", {
      provider: "gemini-2.5-flash",
      elapsedMs: 210
    });
    emitKpi("provider.gemini.health", "failure", {
      provider: "gemini-2.5-flash",
      elapsedMs: 900,
      error: "Gemini transcription failed with status 503."
    });
    const app = createApp({ webOrigin: "http://localhost:3000" });
    const server = createServer(app);
    activeServers.add(server);

    await new Promise<void>((resolve) => {
      server.listen(0, "127.0.0.1", () => resolve());
    });

    const address = server.address();
    if (!address || typeof address === "string") {
      throw new Error("Expected the test server to bind to a TCP port.");
    }

    const response = await fetch(
      `http://127.0.0.1:${address.port}/public/kpi/providers?window=today`
    );
    expect(response.status).toBe(200);
    const payload = (await response.json()) as {
      providers: Array<{
        provider: string;
        avgLatencyMs: number | null;
        p95LatencyMs: number | null;
        topErrors: Array<{ reason: string; count: number }>;
      }>;
    };

    expect(payload.providers[0]?.provider).toBe("gemini-2.5-flash");
    expect(payload.providers[0]?.avgLatencyMs).not.toBeNull();
    expect(payload.providers[0]?.topErrors[0]?.reason).toContain("503");
  });

  it("serves transcription and report-context pipeline stage diagnostics", async () => {
    emitKpi("session.transcription.queue", "success", { elapsedMs: 50 });
    emitKpi("session.transcription.provider_call", "failure", {
      elapsedMs: 1800,
      error: "OpenAI transcription failed with status 500."
    });
    emitKpi("report_context.preprocess", "success", {
      elapsedMs: 120,
      unitCount: 3
    });
    const app = createApp({ webOrigin: "http://localhost:3000" });
    const server = createServer(app);
    activeServers.add(server);

    await new Promise<void>((resolve) => {
      server.listen(0, "127.0.0.1", () => resolve());
    });

    const address = server.address();
    if (!address || typeof address === "string") {
      throw new Error("Expected the test server to bind to a TCP port.");
    }

    const response = await fetch(
      `http://127.0.0.1:${address.port}/public/kpi/pipeline?window=today`
    );
    expect(response.status).toBe(200);
    const payload = (await response.json()) as {
      stages: Array<{ metric: string; failure: number }>;
    };

    expect(
      payload.stages.some(
        (stage) =>
          stage.metric === "session.transcription.provider_call" &&
          stage.failure >= 1
      )
    ).toBe(true);
    expect(payload.stages.some((stage) => stage.metric === "report_context.preprocess")).toBe(true);
    expect(payload.stages.some((stage) => stage.metric === "report_context.extract_parse")).toBe(true);
  });

  it("serves report-context fallback diagnostics with fallback rate breakdown", async () => {
    emitKpi("report_context.fallback_path", "success", {
      provider: "gemini-2.5-flash-report-context",
      operation: "extract_chunks",
      fallbackUsed: false,
      fallbackProviderChain: "gemini->openai"
    });
    emitKpi("report_context.fallback_path", "success", {
      provider: "gpt-5.4-mini-report-context",
      operation: "extract_chunks",
      fallbackUsed: true,
      fallbackProviderChain: "gemini->openai"
    });
    emitKpi("report_context.fallback_path", "failure", {
      provider: "gpt-5.4-mini-report-context",
      operation: "suggest_draft",
      fallbackUsed: true,
      fallbackProviderChain: "gemini->openai",
      error: "REPORT_CONTEXT_PROVIDER_TIMEOUT"
    });

    const app = createApp({ webOrigin: "http://localhost:3000" });
    const server = createServer(app);
    activeServers.add(server);

    await new Promise<void>((resolve) => {
      server.listen(0, "127.0.0.1", () => resolve());
    });

    const address = server.address();
    if (!address || typeof address === "string") {
      throw new Error("Expected the test server to bind to a TCP port.");
    }

    const response = await fetch(
      `http://127.0.0.1:${address.port}/public/kpi/report-context-fallback?window=today`
    );
    expect(response.status).toBe(200);
    const payload = (await response.json()) as {
      summary: { total: number; fallbackUsed: number; fallbackRate: number };
      byOperation: Array<{ operation: string; total: number }>;
    };

    expect(payload.summary.total).toBeGreaterThanOrEqual(3);
    expect(payload.summary.fallbackUsed).toBeGreaterThanOrEqual(2);
    expect(payload.summary.fallbackRate).toBeGreaterThan(0);
    expect(payload.byOperation.some((entry) => entry.operation === "extract_chunks")).toBe(true);
    expect(payload.byOperation.some((entry) => entry.operation === "suggest_draft")).toBe(true);
  });

  it("serves KPI event trends with traffic and stage latency buckets", async () => {
    emitKpi("session.transcription.provider_call", "success", {
      elapsedMs: 1200
    });
    emitKpi("session.transcription.provider_call", "failure", {
      elapsedMs: 1800,
      error: "OpenAI transcription failed with status 500."
    });
    emitKpi("report_context.provider_call", "success", {
      elapsedMs: 600
    });

    const app = createApp({ webOrigin: "http://localhost:3000" });
    const server = createServer(app);
    activeServers.add(server);

    await new Promise<void>((resolve) => {
      server.listen(0, "127.0.0.1", () => resolve());
    });

    const address = server.address();
    if (!address || typeof address === "string") {
      throw new Error("Expected the test server to bind to a TCP port.");
    }

    const response = await fetch(
      `http://127.0.0.1:${address.port}/public/kpi/event-trends?window=today`
    );
    expect(response.status).toBe(200);
    const payload = (await response.json()) as {
      points: Array<{ totalEvents: number; failures: number }>;
      stageLatency: Array<{ metric: string; points: Array<{ avgLatencyMs: number }> }>;
    };

    expect(payload.points.some((point) => point.totalEvents >= 3)).toBe(true);
    expect(payload.points.some((point) => point.failures >= 1)).toBe(true);
    expect(
      payload.stageLatency.some(
        (entry) =>
          entry.metric === "session.transcription.provider_call" &&
          entry.points.some((point) => point.avgLatencyMs > 0)
      )
    ).toBe(true);
  });

  it("serves dev CORS headers for the configured web origin", async () => {
    const originalWebOrigin = process.env.WEB_ORIGIN;
    process.env.WEB_ORIGIN = "http://localhost:3000";
    try {
      const app = createApp({ webOrigin: process.env.WEB_ORIGIN });
      const server = createServer(app);
      activeServers.add(server);

      await new Promise<void>((resolve) => {
        server.listen(0, "127.0.0.1", () => resolve());
      });

      const address = server.address();

      if (!address || typeof address === "string") {
        throw new Error("Expected the test server to bind to a TCP port.");
      }

      const response = await fetch(`http://127.0.0.1:${address.port}/health`, {
        method: "OPTIONS"
      });

      expect(response.status).toBe(204);
      expect(response.headers.get("access-control-allow-origin")).toBe(
        "http://localhost:3000"
      );
      expect(response.headers.get("access-control-allow-methods")).toContain(
        "OPTIONS"
      );
      expect(response.headers.get("access-control-allow-methods")).toContain(
        "PUT"
      );
      expect(response.headers.get("access-control-allow-methods")).toContain(
        "DELETE"
      );
      expect(response.headers.get("access-control-allow-headers")).toContain(
        "X-Audio-File-Name"
      );
      expect(response.headers.get("access-control-allow-headers")).toContain(
        "X-Idempotency-Key"
      );
      expect(response.headers.get("access-control-allow-headers")).toContain(
        "X-Report-File-Name"
      );
    } finally {
      if (originalWebOrigin === undefined) {
        delete process.env.WEB_ORIGIN;
      } else {
        process.env.WEB_ORIGIN = originalWebOrigin;
      }
    }
  });

  it("returns only public doctor fields from the unauthenticated doctor list", async () => {
    const doctorRepository = new InMemoryDoctorRepository();
    await doctorRepository.createOrUpdateDoctor({
      name: "Dr. Pooja Rao",
      department: "Cardiology",
      phoneNumber: "9876543210",
      email: "pooja.rao@example.com"
    });
    const authService = new AuthService(doctorRepository, testJwtSecret);
    const app = createApp({
      authService,
      webOrigin: "http://localhost:3000"
    });
    const server = createServer(app);
    activeServers.add(server);

    await new Promise<void>((resolve) => {
      server.listen(0, "127.0.0.1", () => resolve());
    });

    const address = server.address();

    if (!address || typeof address === "string") {
      throw new Error("Expected the test server to bind to a TCP port.");
    }

    const response = await fetch(`http://127.0.0.1:${address.port}/doctors`);
    expect(response.status).toBe(200);

    const payload = (await response.json()) as {
      doctors: Array<Record<string, unknown>>;
    };
    expect(payload.doctors).toHaveLength(1);
    expect(payload.doctors[0]).toMatchObject({
      name: "Dr. Pooja Rao",
      department: "Cardiology"
    });
    expect(payload.doctors[0]).not.toHaveProperty("phoneNumber");
    expect(payload.doctors[0]).not.toHaveProperty("email");
    expect(payload.doctors[0]).not.toHaveProperty("createdAt");
    expect(payload.doctors[0]).not.toHaveProperty("lastLoginAt");
  });

  it("does not expose internal routes until the internal API is enabled", async () => {
    const app = createApp({
      webOrigin: "http://localhost:3000"
    });
    const server = createServer(app);
    activeServers.add(server);

    await new Promise<void>((resolve) => {
      server.listen(0, "127.0.0.1", () => resolve());
    });

    const address = server.address();

    if (!address || typeof address === "string") {
      throw new Error("Expected the test server to bind to a TCP port.");
    }

    const response = await fetch(`http://127.0.0.1:${address.port}/internal/health`);

    expect(response.status).toBe(404);
  });

  it("protects internal health with a shared API key", async () => {
    const app = createApp({
      webOrigin: "http://localhost:3000",
      internalApi: {
        enabled: true,
        apiKeys: ["internal-secret"]
      }
    });
    const server = createServer(app);
    activeServers.add(server);

    await new Promise<void>((resolve) => {
      server.listen(0, "127.0.0.1", () => resolve());
    });

    const address = server.address();

    if (!address || typeof address === "string") {
      throw new Error("Expected the test server to bind to a TCP port.");
    }

    const unauthorized = await fetch(
      `http://127.0.0.1:${address.port}/internal/health`
    );
    expect(unauthorized.status).toBe(401);

    const authorized = await fetch(
      `http://127.0.0.1:${address.port}/internal/health`,
      {
        headers: {
          "X-Internal-API-Key": "internal-secret",
          "X-Internal-Caller": "admin-dashboard",
          "X-Admin-User-Id": "admin-42",
          "X-Admin-User-Email": "ops@fettleconnect.com"
        }
      }
    );
    expect(authorized.status).toBe(200);

    const payload = (await authorized.json()) as {
      internalApi?: {
        authenticated: boolean;
        authenticationMethod: string;
        caller?: string;
        adminUserId?: string;
        adminUserEmail?: string;
      };
    };

    expect(payload.internalApi).toMatchObject({
      authenticated: true,
      authenticationMethod: "x-internal-api-key",
      caller: "admin-dashboard",
      adminUserId: "admin-42",
      adminUserEmail: "ops@fettleconnect.com"
    });
  });

  it("serves internal reliability alerts from live KPI ratings", async () => {
    emitKpi("session.transcription.end_to_end", "failure", { elapsedMs: 1500 });
    const app = createApp({
      webOrigin: "http://localhost:3000",
      internalApi: {
        enabled: true,
        apiKeys: [testInternalApiKey]
      }
    });
    const server = createServer(app);
    activeServers.add(server);

    await new Promise<void>((resolve) => {
      server.listen(0, "127.0.0.1", () => resolve());
    });

    const address = server.address();

    if (!address || typeof address === "string") {
      throw new Error("Expected the test server to bind to a TCP port.");
    }

    const response = await fetch(
      `http://127.0.0.1:${address.port}/internal/admin/reliability/alerts`,
      {
        headers: {
          "X-Internal-API-Key": testInternalApiKey
        }
      }
    );

    expect(response.status).toBe(200);
    const payload = (await response.json()) as {
      alerts: Array<{ metric?: string; rating: string }>;
    };
    expect(payload.alerts.some((alert) =>
      alert.metric === "session.transcription.end_to_end" &&
      (alert.rating === "critical" || alert.rating === "risky")
    )).toBe(true);
  });

  it("serves internal Fettle sync stats, dry-run, and job summaries", async () => {
    const fettleSyncService = new FettleSyncService(
      new InMemoryFettleSyncRepository(),
      {
        enabled: false,
        timeoutMs: 1000,
        maxAttempts: 1,
        retryWindowMs: 60_000,
        baseRetryDelayMs: 1,
        staleProcessingMs: 60_000
      }
    );
    const app = createApp({
      webOrigin: "http://localhost:3000",
      internalApi: {
        enabled: true,
        apiKeys: [testInternalApiKey]
      },
      fettleSyncService
    });
    const server = createServer(app);
    activeServers.add(server);

    await new Promise<void>((resolve) => {
      server.listen(0, "127.0.0.1", () => resolve());
    });

    const address = server.address();

    if (!address || typeof address === "string") {
      throw new Error("Expected the test server to bind to a TCP port.");
    }

    const headers = { "X-Internal-API-Key": testInternalApiKey };
    const dryRun = await fetch(
      `http://127.0.0.1:${address.port}/internal/admin/fettle-sync/dry-run`,
      { method: "POST", headers }
    );
    expect(dryRun.status).toBe(202);
    const dryRunPayload = (await dryRun.json()) as {
      result: { processed: number; succeeded: number };
    };
    expect(dryRunPayload.result).toMatchObject({ processed: 1, succeeded: 1 });

    const stats = await fetch(
      `http://127.0.0.1:${address.port}/internal/admin/fettle-sync/stats`,
      { headers }
    );
    expect(stats.status).toBe(200);
    const statsPayload = (await stats.json()) as { enabled: boolean; succeeded: number };
    expect(statsPayload.enabled).toBe(false);
    expect(statsPayload.succeeded).toBe(1);

    const jobs = await fetch(
      `http://127.0.0.1:${address.port}/internal/admin/fettle-sync/jobs?status=succeeded`,
      { headers }
    );
    expect(jobs.status).toBe(200);
    const jobsPayload = (await jobs.json()) as { jobs: Array<{ status: string; payload?: unknown }> };
    expect(jobsPayload.jobs[0]?.status).toBe("succeeded");
    expect(jobsPayload.jobs[0]).not.toHaveProperty("payload");
  });

  it("rejects doctor bearer tokens on internal routes", async () => {
    const { authService, token } = await createAuthenticatedAuthService();
    const app = createApp({
      authService,
      webOrigin: "http://localhost:3000",
      internalApi: {
        enabled: true,
        apiKeys: ["internal-secret"]
      }
    });
    const server = createServer(app);
    activeServers.add(server);

    await new Promise<void>((resolve) => {
      server.listen(0, "127.0.0.1", () => resolve());
    });

    const address = server.address();

    if (!address || typeof address === "string") {
      throw new Error("Expected the test server to bind to a TCP port.");
    }

    const response = await fetch(
      `http://127.0.0.1:${address.port}/internal/health`,
      {
        headers: {
          Authorization: `Bearer ${token}`
        }
      }
    );

    expect(response.status).toBe(401);
  });

  it("supports admin doctor creation and lookup on internal routes", async () => {
    const app = createApp({
      webOrigin: "http://localhost:3000",
      internalApi: {
        enabled: true,
        apiKeys: [testInternalApiKey]
      }
    });
    const server = createServer(app);
    activeServers.add(server);

    await new Promise<void>((resolve) => {
      server.listen(0, "127.0.0.1", () => resolve());
    });

    const address = server.address();

    if (!address || typeof address === "string") {
      throw new Error("Expected the test server to bind to a TCP port.");
    }

    const createResponse = await fetch(
      `http://127.0.0.1:${address.port}/internal/admin/doctors`,
      {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "X-Internal-API-Key": testInternalApiKey
        },
        body: JSON.stringify({
          name: "Dr. Admin Created",
          department: "Cardiology",
          phoneNumber: "9123456789"
        })
      }
    );
    expect(createResponse.status).toBe(201);

    const created = (await createResponse.json()) as {
      doctor: { id: string; name: string };
    };
    expect(created.doctor.name).toBe("Dr. Admin Created");

    const listResponse = await fetch(
      `http://127.0.0.1:${address.port}/internal/admin/doctors`,
      {
        headers: {
          "X-Internal-API-Key": testInternalApiKey
        }
      }
    );
    expect(listResponse.status).toBe(200);
    const listed = (await listResponse.json()) as {
      doctors: Array<{ id: string }>;
    };
    expect(listed.doctors.some((doctor) => doctor.id === created.doctor.id)).toBe(
      true
    );

    const detailResponse = await fetch(
      `http://127.0.0.1:${address.port}/internal/admin/doctors/${created.doctor.id}`,
      {
        headers: {
          "X-Internal-API-Key": testInternalApiKey
        }
      }
    );
    expect(detailResponse.status).toBe(200);
    const detail = (await detailResponse.json()) as {
      doctor: Record<string, unknown>;
    };
    expect(detail.doctor).not.toHaveProperty("pinHash");
    expect(detail.doctor).not.toHaveProperty("tokenVersion");
  });

  it("supports admin session transcript and pdf access on internal routes", async () => {
    const { authService, doctor } = await createAuthenticatedAuthService();
    const sessionService = new SessionService(
      new InMemorySessionRepository(),
      new MockGeminiTranscriptionAdapter()
    );
    const session = await sessionService.createSession(
      {
        patient: {
          name: "Anita Sharma",
          phoneNumber: "9876543210"
        }
      },
      doctor
    );
    await sessionService.uploadSessionAudio(
      session.id,
      {
        fileName: "consultation.webm",
        mimeType: "audio/webm",
        sizeBytes: 5,
        durationSeconds: 12,
        data: Buffer.from("audio")
      },
      doctor.id
    );
    await sessionService.submitSessionTranscription(session.id, doctor.id);
    await sessionService.generateSessionPdf(session.id, doctor.id);

    const app = createApp({
      sessionService,
      authService,
      webOrigin: "http://localhost:3000",
      internalApi: {
        enabled: true,
        apiKeys: [testInternalApiKey]
      }
    });
    const server = createServer(app);
    activeServers.add(server);

    await new Promise<void>((resolve) => {
      server.listen(0, "127.0.0.1", () => resolve());
    });

    const address = server.address();

    if (!address || typeof address === "string") {
      throw new Error("Expected the test server to bind to a TCP port.");
    }

    const transcriptionResponse = await fetch(
      `http://127.0.0.1:${address.port}/internal/admin/sessions/${session.id}/transcription`,
      {
        headers: {
          "X-Internal-API-Key": testInternalApiKey
        }
      }
    );
    expect(transcriptionResponse.status).toBe(200);

    const pdfResponse = await fetch(
      `http://127.0.0.1:${address.port}/internal/admin/sessions/${session.id}/artifacts/pdf`,
      {
        headers: {
          "X-Internal-API-Key": testInternalApiKey
        }
      }
    );
    expect(pdfResponse.status).toBe(200);
    expect(pdfResponse.headers.get("content-type")).toContain("application/pdf");
  });

  it("filters internal admin sessions with hasDocument=false correctly", async () => {
    const { authService, doctor } = await createAuthenticatedAuthService();
    const sessionService = new SessionService(
      new InMemorySessionRepository(),
      new MockGeminiTranscriptionAdapter()
    );
    const sessionWithDocument = await sessionService.createSession(
      {
        patient: {
          name: "Anita Sharma",
          phoneNumber: "9876543210"
        }
      },
      doctor
    );
    const sessionWithoutDocument = await sessionService.createSession(
      {
        patient: {
          name: "Ravi Kumar",
          phoneNumber: "9988776655"
        }
      },
      doctor
    );

    await sessionService.uploadSessionAudio(
      sessionWithDocument.id,
      {
        fileName: "consultation.webm",
        mimeType: "audio/webm",
        sizeBytes: 5,
        durationSeconds: 12,
        data: Buffer.from("audio")
      },
      doctor.id
    );
    await sessionService.submitSessionTranscription(sessionWithDocument.id, doctor.id);
    await sessionService.generateSessionPdf(sessionWithDocument.id, doctor.id);

    const app = createApp({
      sessionService,
      authService,
      webOrigin: "http://localhost:3000",
      internalApi: {
        enabled: true,
        apiKeys: [testInternalApiKey]
      }
    });
    const server = createServer(app);
    activeServers.add(server);

    await new Promise<void>((resolve) => {
      server.listen(0, "127.0.0.1", () => resolve());
    });

    const address = server.address();

    if (!address || typeof address === "string") {
      throw new Error("Expected the test server to bind to a TCP port.");
    }

    const falseResponse = await fetch(
      `http://127.0.0.1:${address.port}/internal/admin/sessions?hasDocument=false`,
      {
        headers: {
          "X-Internal-API-Key": testInternalApiKey
        }
      }
    );
    expect(falseResponse.status).toBe(200);
    const falsePayload = (await falseResponse.json()) as {
      sessions: Array<{ id: string }>;
    };
    expect(falsePayload.sessions.map((session) => session.id)).toContain(
      sessionWithoutDocument.id
    );
    expect(falsePayload.sessions.map((session) => session.id)).not.toContain(
      sessionWithDocument.id
    );

    const zeroResponse = await fetch(
      `http://127.0.0.1:${address.port}/internal/admin/sessions?hasDocument=0`,
      {
        headers: {
          "X-Internal-API-Key": testInternalApiKey
        }
      }
    );
    expect(zeroResponse.status).toBe(200);
    const zeroPayload = (await zeroResponse.json()) as {
      sessions: Array<{ id: string }>;
    };
    expect(zeroPayload.sessions.map((session) => session.id)).toContain(
      sessionWithoutDocument.id
    );
    expect(zeroPayload.sessions.map((session) => session.id)).not.toContain(
      sessionWithDocument.id
    );
  });

  it("fetches internal admin pdf delivery jobs by id directly", async () => {
    const expectedJob = {
      id: "11111111-1111-4111-8111-111111111111",
      sessionId: "22222222-2222-4222-8222-222222222222",
      recipientType: "patient",
      recipientName: "Anita Sharma",
      phoneNumber: "9876543210",
      status: "pending",
      attemptCount: 0,
      createdAt: new Date().toISOString(),
      updatedAt: new Date().toISOString()
    } as const;

    const app = createApp({
      webOrigin: "http://localhost:3000",
      internalApi: {
        enabled: true,
        apiKeys: [testInternalApiKey]
      },
      pdfDeliveryService: {
        async listJobsForAdmin() {
          return [];
        },
        async getJobByIdForAdmin(jobId: string) {
          return jobId === expectedJob.id ? expectedJob : null;
        }
      }
    });
    const server = createServer(app);
    activeServers.add(server);

    await new Promise<void>((resolve) => {
      server.listen(0, "127.0.0.1", () => resolve());
    });

    const address = server.address();

    if (!address || typeof address === "string") {
      throw new Error("Expected the test server to bind to a TCP port.");
    }

    const response = await fetch(
      `http://127.0.0.1:${address.port}/internal/admin/pdf-delivery/jobs/${expectedJob.id}`,
      {
        headers: {
          "X-Internal-API-Key": testInternalApiKey
        }
      }
    );

    expect(response.status).toBe(200);
    const payload = (await response.json()) as {
      job: { id: string; sessionId: string };
    };
    expect(payload.job).toMatchObject({
      id: expectedJob.id,
      sessionId: expectedJob.sessionId
    });
  });

  it("supports admin queue listing and acceptance on internal routes", async () => {
    const { authService, doctor } = await createAuthenticatedAuthService();
    const sessionService = new SessionService(new InMemorySessionRepository());
    const queueService = new QueueService(
      new InMemoryQueueRepository(),
      sessionService
    );
    const intakeLink = await queueService.getDoctorIntakeLink(doctor);
    const createdEntry = await queueService.submitPublicIntake(intakeLink.token, {
      patient: {
        name: "Ravi Kumar",
        phoneNumber: "9988776655",
        age: 41
      }
    });

    const app = createApp({
      authService,
      queueService,
      sessionService,
      webOrigin: "http://localhost:3000",
      internalApi: {
        enabled: true,
        apiKeys: [testInternalApiKey]
      }
    });
    const server = createServer(app);
    activeServers.add(server);

    await new Promise<void>((resolve) => {
      server.listen(0, "127.0.0.1", () => resolve());
    });

    const address = server.address();

    if (!address || typeof address === "string") {
      throw new Error("Expected the test server to bind to a TCP port.");
    }

    const listResponse = await fetch(
      `http://127.0.0.1:${address.port}/internal/admin/queue`,
      {
        headers: {
          "X-Internal-API-Key": testInternalApiKey
        }
      }
    );
    expect(listResponse.status).toBe(200);

    const listed = (await listResponse.json()) as { entries: Array<{ id: string }> };
    expect(listed.entries.some((entry) => entry.id === createdEntry.id)).toBe(true);

    const acceptResponse = await fetch(
      `http://127.0.0.1:${address.port}/internal/admin/queue/${createdEntry.id}/accept`,
      {
        method: "POST",
        headers: {
          "X-Internal-API-Key": testInternalApiKey
        }
      }
    );
    expect(acceptResponse.status).toBe(201);
  });

  it("uploads session audio through the raw audio route", async () => {
    const { authService, doctor, token } = await createAuthenticatedAuthService();
    const sessionService = new SessionService(new InMemorySessionRepository());
    const session = await sessionService.createSession({
      patient: {
        name: "Anita Sharma",
        phoneNumber: "9876543210"
      },
    }, doctor);

    const app = createApp({
      sessionService,
      authService,
      webOrigin: "http://localhost:3000"
    });
    const server = createServer(app);
    activeServers.add(server);

    await new Promise<void>((resolve) => {
      server.listen(0, "127.0.0.1", () => resolve());
    });

    const address = server.address();

    if (!address || typeof address === "string") {
      throw new Error("Expected the test server to bind to a TCP port.");
    }

    const response = await fetch(
      `http://127.0.0.1:${address.port}/sessions/${session.id}/audio`,
      {
        method: "POST",
        headers: {
          Authorization: `Bearer ${token}`,
          "Content-Type": "audio/webm",
          "X-Audio-Duration-Seconds": "12.5",
          "X-Audio-File-Name": "consultation.webm"
        },
        body: Buffer.from("audio")
      }
    );

    expect(response.status).toBe(201);

    const payload = (await response.json()) as {
      session: { audioArtifact?: { fileName: string; durationSeconds: number } };
    };

    expect(payload.session.audioArtifact).toMatchObject({
      fileName: "consultation.webm",
      durationSeconds: 12.5
    });
  });

  it("supports public intake submissions and doctor acceptance", async () => {
    const { authService, doctor, token } = await createAuthenticatedAuthService();
    const sessionService = new SessionService(new InMemorySessionRepository());
    const queueService = new QueueService(
      new InMemoryQueueRepository(),
      sessionService
    );
    const intakeLink = await queueService.getDoctorIntakeLink(doctor);
    const app = createApp({
      sessionService,
      queueService,
      authService,
      webOrigin: "http://localhost:3000"
    });
    const server = createServer(app);
    activeServers.add(server);

    await new Promise<void>((resolve) => {
      server.listen(0, "127.0.0.1", () => resolve());
    });

    const address = server.address();

    if (!address || typeof address === "string") {
      throw new Error("Expected the test server to bind to a TCP port.");
    }

    const intakeResponse = await fetch(
      `http://127.0.0.1:${address.port}/public/intake/${intakeLink.token}`,
      {
        method: "POST",
        headers: {
          "Content-Type": "application/json"
        },
        body: JSON.stringify({
          patient: {
            name: "Anita Sharma",
            phoneNumber: "9876543210",
            age: 32
          }
        })
      }
    );

    expect(intakeResponse.status).toBe(201);

    const intakePayload = (await intakeResponse.json()) as {
      entry: { id: string; patient: { age: number } };
    };

    expect(intakePayload.entry.patient.age).toBe(32);

    const queueResponse = await fetch(`http://127.0.0.1:${address.port}/queue`, {
      headers: {
        Authorization: `Bearer ${token}`
      }
    });

    expect(queueResponse.status).toBe(200);

    const queuePayload = (await queueResponse.json()) as {
      entries: Array<{ id: string; patient: { name: string } }>;
    };

    expect(queuePayload.entries).toHaveLength(1);
    expect(queuePayload.entries[0]?.patient.name).toBe("Anita Sharma");

    const acceptResponse = await fetch(
      `http://127.0.0.1:${address.port}/queue/${intakePayload.entry.id}/accept`,
      {
        method: "POST",
        headers: {
          Authorization: `Bearer ${token}`
        }
      }
    );

    expect(acceptResponse.status).toBe(201);

    const acceptPayload = (await acceptResponse.json()) as {
      entry: { status: string; sessionId?: string };
      session: { patient: { name: string; age?: number } };
    };

    expect(acceptPayload.entry.status).toBe("accepted");
    expect(acceptPayload.entry.sessionId).toBeTruthy();
    expect(acceptPayload.session.patient).toMatchObject({
      name: "Anita Sharma",
      age: 32
    });
  });

  it("supports marking waiting patients as no-show and cancelled", async () => {
    const { authService, doctor, token } = await createAuthenticatedAuthService();
    const sessionService = new SessionService(new InMemorySessionRepository());
    const queueService = new QueueService(
      new InMemoryQueueRepository(),
      sessionService
    );
    const intakeLink = await queueService.getDoctorIntakeLink(doctor);
    const app = createApp({
      sessionService,
      queueService,
      authService,
      webOrigin: "http://localhost:3000"
    });
    const server = createServer(app);
    activeServers.add(server);

    await new Promise<void>((resolve) => {
      server.listen(0, "127.0.0.1", () => resolve());
    });

    const address = server.address();

    if (!address || typeof address === "string") {
      throw new Error("Expected the test server to bind to a TCP port.");
    }

    const firstIntakeResponse = await fetch(
      `http://127.0.0.1:${address.port}/public/intake/${intakeLink.token}`,
      {
        method: "POST",
        headers: {
          "Content-Type": "application/json"
        },
        body: JSON.stringify({
          patient: {
            name: "Rahul Singh",
            phoneNumber: "9999999991",
            age: 41
          }
        })
      }
    );

    const secondIntakeResponse = await fetch(
      `http://127.0.0.1:${address.port}/public/intake/${intakeLink.token}`,
      {
        method: "POST",
        headers: {
          "Content-Type": "application/json"
        },
        body: JSON.stringify({
          patient: {
            name: "Meera Nair",
            phoneNumber: "9999999992",
            age: 28
          }
        })
      }
    );

    expect(firstIntakeResponse.status).toBe(201);
    expect(secondIntakeResponse.status).toBe(201);

    const firstEntry = (await firstIntakeResponse.json()) as { entry: { id: string } };
    const secondEntry = (await secondIntakeResponse.json()) as { entry: { id: string } };

    const noShowResponse = await fetch(
      `http://127.0.0.1:${address.port}/queue/${firstEntry.entry.id}/no-show`,
      {
        method: "POST",
        headers: {
          Authorization: `Bearer ${token}`
        }
      }
    );

    const cancelResponse = await fetch(
      `http://127.0.0.1:${address.port}/queue/${secondEntry.entry.id}/cancel`,
      {
        method: "POST",
        headers: {
          Authorization: `Bearer ${token}`
        }
      }
    );

    expect(noShowResponse.status).toBe(200);
    expect(cancelResponse.status).toBe(200);

    const noShowPayload = (await noShowResponse.json()) as {
      entry: { status: string };
    };
    const cancelPayload = (await cancelResponse.json()) as {
      entry: { status: string };
    };

    expect(noShowPayload.entry.status).toBe("no_show");
    expect(cancelPayload.entry.status).toBe("cancelled");

    const queueResponse = await fetch(`http://127.0.0.1:${address.port}/queue`, {
      headers: {
        Authorization: `Bearer ${token}`
      }
    });

    expect(queueResponse.status).toBe(200);

    const queuePayload = (await queueResponse.json()) as {
      entries: Array<{ id: string }>;
    };

    expect(queuePayload.entries).toHaveLength(0);
  });

  it("returns the existing waiting entry for repeated intake submissions", async () => {
    const { authService, doctor, token } = await createAuthenticatedAuthService();
    const sessionService = new SessionService(new InMemorySessionRepository());
    const queueService = new QueueService(
      new InMemoryQueueRepository(),
      sessionService
    );
    const intakeLink = await queueService.getDoctorIntakeLink(doctor);
    const app = createApp({
      sessionService,
      queueService,
      authService,
      webOrigin: "http://localhost:3000"
    });
    const server = createServer(app);
    activeServers.add(server);

    await new Promise<void>((resolve) => {
      server.listen(0, "127.0.0.1", () => resolve());
    });

    const address = server.address();

    if (!address || typeof address === "string") {
      throw new Error("Expected the test server to bind to a TCP port.");
    }

    const payload = JSON.stringify({
      patient: {
        name: "Anita Sharma",
        phoneNumber: "9876543210",
        age: 32
      }
    });

    const firstResponse = await fetch(
      `http://127.0.0.1:${address.port}/public/intake/${intakeLink.token}`,
      {
        method: "POST",
        headers: {
          "Content-Type": "application/json"
        },
        body: payload
      }
    );

    const secondResponse = await fetch(
      `http://127.0.0.1:${address.port}/public/intake/${intakeLink.token}`,
      {
        method: "POST",
        headers: {
          "Content-Type": "application/json"
        },
        body: payload
      }
    );

    expect(firstResponse.status).toBe(201);
    expect(secondResponse.status).toBe(201);

    const firstPayload = (await firstResponse.json()) as {
      entry: { id: string };
    };
    const secondPayload = (await secondResponse.json()) as {
      entry: { id: string };
    };

    expect(secondPayload.entry.id).toBe(firstPayload.entry.id);

    const queueResponse = await fetch(`http://127.0.0.1:${address.port}/queue`, {
      headers: {
        Authorization: `Bearer ${token}`
      }
    });

    const queuePayload = (await queueResponse.json()) as {
      entries: Array<{ id: string }>;
    };

    expect(queuePayload.entries).toHaveLength(1);
    expect(queuePayload.entries[0]?.id).toBe(firstPayload.entry.id);
  });

  it("rejects a second queue status action after the entry has already changed", async () => {
    const { authService, doctor, token } = await createAuthenticatedAuthService();
    const sessionService = new SessionService(new InMemorySessionRepository());
    const queueService = new QueueService(
      new InMemoryQueueRepository(),
      sessionService
    );
    const intakeLink = await queueService.getDoctorIntakeLink(doctor);
    const app = createApp({
      sessionService,
      queueService,
      authService,
      webOrigin: "http://localhost:3000"
    });
    const server = createServer(app);
    activeServers.add(server);

    await new Promise<void>((resolve) => {
      server.listen(0, "127.0.0.1", () => resolve());
    });

    const address = server.address();

    if (!address || typeof address === "string") {
      throw new Error("Expected the test server to bind to a TCP port.");
    }

    const intakeResponse = await fetch(
      `http://127.0.0.1:${address.port}/public/intake/${intakeLink.token}`,
      {
        method: "POST",
        headers: {
          "Content-Type": "application/json"
        },
        body: JSON.stringify({
          patient: {
            name: "Riya Shah",
            phoneNumber: "9999999988",
            age: 25
          }
        })
      }
    );

    const intakePayload = (await intakeResponse.json()) as {
      entry: { id: string };
    };

    const firstActionResponse = await fetch(
      `http://127.0.0.1:${address.port}/queue/${intakePayload.entry.id}/no-show`,
      {
        method: "POST",
        headers: {
          Authorization: `Bearer ${token}`
        }
      }
    );

    const secondActionResponse = await fetch(
      `http://127.0.0.1:${address.port}/queue/${intakePayload.entry.id}/cancel`,
      {
        method: "POST",
        headers: {
          Authorization: `Bearer ${token}`
        }
      }
    );

    expect(firstActionResponse.status).toBe(200);
    expect(secondActionResponse.status).toBe(409);

    const secondActionPayload = (await secondActionResponse.json()) as {
      error: string;
    };

    expect(secondActionPayload.error).toContain("no longer waiting");
  });

  it("rejects negative audio duration headers", async () => {
    const { authService, doctor, token } = await createAuthenticatedAuthService();
    const sessionService = new SessionService(new InMemorySessionRepository());
    const session = await sessionService.createSession({
      patient: {
        name: "Anita Sharma",
        phoneNumber: "9876543210"
      },
    }, doctor);

    const app = createApp({
      sessionService,
      authService,
      webOrigin: "http://localhost:3000"
    });
    const server = createServer(app);
    activeServers.add(server);

    await new Promise<void>((resolve) => {
      server.listen(0, "127.0.0.1", () => resolve());
    });

    const address = server.address();

    if (!address || typeof address === "string") {
      throw new Error("Expected the test server to bind to a TCP port.");
    }

    const response = await fetch(
      `http://127.0.0.1:${address.port}/sessions/${session.id}/audio`,
      {
        method: "POST",
        headers: {
          Authorization: `Bearer ${token}`,
          "Content-Type": "audio/webm",
          "X-Audio-Duration-Seconds": "-5",
          "X-Audio-File-Name": "consultation.webm"
        },
        body: Buffer.from("audio")
      }
    );

    expect(response.status).toBe(400);
  });

  it("returns same-doctor previous patient sessions by phone number", async () => {
    const { authService, doctor, token } = await createAuthenticatedAuthService();
    const repository = new InMemorySessionRepository();
    const sessionService = new SessionService(repository);
    const previousSession = await sessionService.createSession(
      {
        patient: {
          name: "Anita Sharma",
          phoneNumber: "9876543210"
        }
      },
      doctor,
      { createdAt: "2026-05-14T09:00:00.000Z" }
    );
    const currentSession = await sessionService.createSession(
      {
        patient: {
          name: "Anita Sharma",
          phoneNumber: "9876543210"
        }
      },
      doctor,
      { createdAt: "2026-05-14T10:00:00.000Z" }
    );
    await repository.markTranscriptionProcessing(
      previousSession.id,
      "test",
      "2026-05-14T09:05:00.000Z"
    );
    await repository.completeTranscription(previousSession.id, {
      provider: "test",
      requiresRecovery: false,
      summary: "Follow-up for cough",
      transcriptSegments: [],
      metadata: {
        chiefComplaint: "Cough",
        symptoms: ["Cough"],
        diagnoses: [],
        careTeamNotes: []
      },
      draftDocument: {
        title: "Consultation Note",
        chiefComplaint: "Cough",
        illnessHistory: "Cough for three days",
        clinicalSummary: "Cough improving after medication.",
        medications: [
          {
            medicationName: "Paracetamol",
            dosage: "500mg",
            instructions: "After food"
          }
        ],
        testsPrescribed: ["CBC"],
        nextSteps: ["Review if fever persists"],
        cautions: []
      }
    });
    await sessionService.createSession(
      {
        patient: {
          name: "Anita Sharma",
          phoneNumber: "9876543210"
        }
      },
      doctor,
      { createdAt: "2026-05-14T11:00:00.000Z" }
    );
    await sessionService.createSession({
      patient: {
        name: "Anita Sharma",
        phoneNumber: "9876543210"
      },
    }, {
      ...doctor,
      id: "316f0eda-fbb5-478e-b15f-c9f18f5bfc12"
    });

    const app = createApp({
      sessionService,
      authService,
      webOrigin: "http://localhost:3000"
    });
    const server = createServer(app);
    activeServers.add(server);

    await new Promise<void>((resolve) => {
      server.listen(0, "127.0.0.1", () => resolve());
    });

    const address = server.address();

    if (!address || typeof address === "string") {
      throw new Error("Expected the test server to bind to a TCP port.");
    }

    const response = await fetch(
      `http://127.0.0.1:${address.port}/sessions/${currentSession.id}/patient-history`,
      {
        headers: {
          Authorization: `Bearer ${token}`
        }
      }
    );

    expect(response.status).toBe(200);

    const payload = (await response.json()) as {
      sessions: Array<{
        id: string;
        clinicalSummary?: string;
        chiefComplaint?: string;
        medicationCount: number;
        testCount: number;
      }>;
    };

    expect(payload.sessions).toHaveLength(1);
    expect(payload.sessions[0]).toMatchObject({
      id: previousSession.id,
      clinicalSummary: "Cough improving after medication.",
      chiefComplaint: "Cough",
      medicationCount: 1,
      testCount: 1
    });
  });

  it("updates session case status for the owning doctor", async () => {
    const { authService, doctor, token } = await createAuthenticatedAuthService();
    const sessionService = new SessionService(new InMemorySessionRepository());
    const previousSession = await sessionService.createSession({
      patient: {
        name: "Anita Sharma",
        phoneNumber: "9876543210"
      },
    }, doctor);
    const session = await sessionService.createSession({
      patient: {
        name: "Anita Sharma",
        phoneNumber: "9876543210"
      },
    }, doctor);

    const app = createApp({
      sessionService,
      authService,
      webOrigin: "http://localhost:3000"
    });
    const server = createServer(app);
    activeServers.add(server);

    await new Promise<void>((resolve) => {
      server.listen(0, "127.0.0.1", () => resolve());
    });

    const address = server.address();

    if (!address || typeof address === "string") {
      throw new Error("Expected the test server to bind to a TCP port.");
    }

    const response = await fetch(
      `http://127.0.0.1:${address.port}/sessions/${session.id}/case-status`,
      {
        method: "PATCH",
        headers: {
          Authorization: `Bearer ${token}`,
          "Content-Type": "application/json"
        },
        body: JSON.stringify({ caseStatus: "closed" })
      }
    );

    expect(response.status).toBe(200);

    const payload = (await response.json()) as {
      session: { id: string; caseStatus: string };
    };

    expect(payload.session).toMatchObject({
      id: session.id,
      caseStatus: "closed"
    });

    await expect(
      sessionService.getSessionById(previousSession.id, doctor.id)
    ).resolves.toMatchObject({
      id: previousSession.id,
      caseStatus: "closed"
    });
  });

  it("sanitizes audio file names before storing them", async () => {
    const { authService, doctor, token } = await createAuthenticatedAuthService();
    const sessionService = new SessionService(new InMemorySessionRepository());
    const session = await sessionService.createSession({
      patient: {
        name: "Anita Sharma",
        phoneNumber: "9876543210"
      },
    }, doctor);

    const app = createApp({
      sessionService,
      authService,
      webOrigin: "http://localhost:3000"
    });
    const server = createServer(app);
    activeServers.add(server);

    await new Promise<void>((resolve) => {
      server.listen(0, "127.0.0.1", () => resolve());
    });

    const address = server.address();

    if (!address || typeof address === "string") {
      throw new Error("Expected the test server to bind to a TCP port.");
    }

    const response = await fetch(
      `http://127.0.0.1:${address.port}/sessions/${session.id}/audio`,
      {
        method: "POST",
        headers: {
          Authorization: `Bearer ${token}`,
          "Content-Type": "audio/webm",
          "X-Audio-Duration-Seconds": "12.5",
          "X-Audio-File-Name": "..\\nested/consultation:webm"
        },
        body: Buffer.from("audio")
      }
    );

    expect(response.status).toBe(201);

    const payload = (await response.json()) as {
      session: { audioArtifact?: { fileName: string } };
    };

    expect(payload.session.audioArtifact?.fileName).toBe(".._nested_consultation_webm");
  });

  it("serves inline pdf disposition when requested for preview", async () => {
    const { authService, doctor, token } = await createAuthenticatedAuthService();
    const sessionService = new SessionService(
      new InMemorySessionRepository(),
      new MockGeminiTranscriptionAdapter()
    );
    const session = await sessionService.createSession({
      patient: {
        name: "Anita Sharma",
        phoneNumber: "9876543210"
      },
    }, doctor);

    await sessionService.uploadSessionAudio(session.id, {
      fileName: "consultation.webm",
      mimeType: "audio/webm",
      sizeBytes: 5,
      durationSeconds: 120,
      data: Buffer.from("audio")
    });
    await sessionService.submitSessionTranscription(session.id);
    await sessionService.generateSessionPdf(session.id);

    const app = createApp({
      sessionService,
      authService,
      webOrigin: "http://localhost:3000"
    });
    const server = createServer(app);
    activeServers.add(server);

    await new Promise<void>((resolve) => {
      server.listen(0, "127.0.0.1", () => resolve());
    });

    const address = server.address();

    if (!address || typeof address === "string") {
      throw new Error("Expected the test server to bind to a TCP port.");
    }

    const response = await fetch(
      `http://127.0.0.1:${address.port}/sessions/${session.id}/artifacts/pdf?disposition=inline`,
      {
        headers: {
          Authorization: `Bearer ${token}`
        }
      }
    );

    expect(response.status).toBe(200);
    expect(response.headers.get("content-disposition")).toContain("inline;");
  });

  it("starts transcription asynchronously and returns processing immediately", async () => {
    const errorSpy = vi.spyOn(console, "error").mockImplementation(() => {});
    const { authService, doctor, token } = await createAuthenticatedAuthService();
    const sessionService = new SessionService(
      new InMemorySessionRepository(),
      new AbortTranscriptionAdapter()
    );
    const session = await sessionService.createSession({
      patient: {
        name: "Anita Sharma",
        phoneNumber: "9876543210"
      },
    }, doctor);

    await sessionService.uploadSessionAudio(session.id, {
      fileName: "consultation.webm",
      mimeType: "audio/webm",
      sizeBytes: 5,
      durationSeconds: 120,
      data: Buffer.from("audio")
    });

    const app = createApp({
      sessionService,
      authService,
      webOrigin: "http://localhost:3000"
    });
    const server = createServer(app);
    activeServers.add(server);

    await new Promise<void>((resolve) => {
      server.listen(0, "127.0.0.1", () => resolve());
    });

    const address = server.address();

    if (!address || typeof address === "string") {
      throw new Error("Expected the test server to bind to a TCP port.");
    }

    const response = await fetch(
      `http://127.0.0.1:${address.port}/sessions/${session.id}/transcription`,
      {
        method: "POST",
        headers: {
          Authorization: `Bearer ${token}`
        }
      }
    );

    expect(response.status).toBe(202);
    await expect(response.json()).resolves.toMatchObject({
      session: {
        id: session.id,
        transcription: {
          status: "processing"
        }
      }
    });

    errorSpy.mockRestore();
  });

  it("streams report extraction progress with retry hints and terminal event", async () => {
    const { authService, doctor, token } = await createAuthenticatedAuthService();
    const repository = new InMemorySessionRepository();
    const sessionService = new SessionService(repository);
    const session = await sessionService.createSession(
      {
        patient: {
          name: "Anita Sharma",
          phoneNumber: "9876543210"
        }
      },
      doctor
    );
    const createdJob = await repository.createReportContextExtractionJob({
      sessionId: session.id,
      uploadIds: ["f6de4e58-f7be-4b71-939a-7e4c8cb81a7d"]
    });
    const now = new Date().toISOString();
    await repository.updateReportContext(session.id, {
      status: "ready",
      chunks: [],
      selectedChunkIds: [],
      appliedChunkIds: [],
      extractedUploadIds: [],
      progress: {
        totalUnits: 2,
        completedUnits: 2,
        failedUnits: 0,
        startedAt: now,
        updatedAt: now,
        currentStage: "Completed",
        etaSecondsLower: 0,
        etaSecondsUpper: 0
      },
      updatedAt: now
    });
    await repository.updateReportContextExtractionJob(session.id, createdJob.id, {
      status: "ready",
      totalUnits: 2,
      completedUnits: 2,
      failedUnits: 0,
      updatedAt: now,
      finishedAt: now
    });

    const app = createApp({
      sessionService,
      authService,
      webOrigin: "http://localhost:3000"
    });
    const server = createServer(app);
    activeServers.add(server);

    await new Promise<void>((resolve) => {
      server.listen(0, "127.0.0.1", () => resolve());
    });

    const address = server.address();
    if (!address || typeof address === "string") {
      throw new Error("Expected the test server to bind to a TCP port.");
    }

    const response = await fetch(
      `http://127.0.0.1:${address.port}/sessions/${session.id}/report-context/jobs/${createdJob.id}/stream`,
      {
        headers: {
          Authorization: `Bearer ${token}`
        }
      }
    );

    expect(response.status).toBe(200);
    expect(response.headers.get("content-type")).toContain("text/event-stream");
    const payload = await response.text();
    expect(payload).toContain("retry: 2000");
    expect(payload).toContain("event: heartbeat");
    expect(payload).toContain("event: terminal");
    const jobEvents = payload
      .split("\n\n")
      .map((eventBlock) =>
        eventBlock
          .split("\n")
          .find((line) => line.startsWith("data: "))
          ?.slice("data: ".length)
      )
      .filter((line): line is string => Boolean(line))
      .map((line) => JSON.parse(line) as { job?: unknown });
    const streamedJob = jobEvents.find(
      (event): event is {
        job: {
          status: string;
          progress?: {
            currentStage?: string;
            etaSecondsLower?: number;
            etaSecondsUpper?: number;
          };
        };
      } => Boolean(event.job)
    )?.job;
    expect(streamedJob?.status).toBe("ready");
    expect(streamedJob?.progress?.currentStage).toBe("Completed");
    expect(streamedJob?.progress?.etaSecondsLower).toBe(0);
    expect(streamedJob?.progress?.etaSecondsUpper).toBe(0);
  });

  it("rejects report extraction stream requests without authentication", async () => {
    const { authService, doctor } = await createAuthenticatedAuthService();
    const repository = new InMemorySessionRepository();
    const sessionService = new SessionService(repository);
    const session = await sessionService.createSession(
      {
        patient: {
          name: "Anita Sharma",
          phoneNumber: "9876543210"
        }
      },
      doctor
    );
    const createdJob = await repository.createReportContextExtractionJob({
      sessionId: session.id,
      uploadIds: ["bf4f81e8-d50d-43d0-a7d9-5d9f2b7f4819"]
    });

    const app = createApp({
      sessionService,
      authService,
      webOrigin: "http://localhost:3000"
    });
    const server = createServer(app);
    activeServers.add(server);

    await new Promise<void>((resolve) => {
      server.listen(0, "127.0.0.1", () => resolve());
    });

    const address = server.address();
    if (!address || typeof address === "string") {
      throw new Error("Expected the test server to bind to a TCP port.");
    }

    const response = await fetch(
      `http://127.0.0.1:${address.port}/sessions/${session.id}/report-context/jobs/${createdJob.id}/stream`
    );
    expect(response.status).toBe(401);
  });

  it("rejects short idempotency keys for transcription start", async () => {
    const { authService, doctor, token } = await createAuthenticatedAuthService();
    const sessionService = new SessionService(
      new InMemorySessionRepository(),
      new AbortTranscriptionAdapter()
    );
    const session = await sessionService.createSession({
      patient: {
        name: "Anita Sharma",
        phoneNumber: "9876543210"
      },
    }, doctor);

    await sessionService.uploadSessionAudio(session.id, {
      fileName: "consultation.webm",
      mimeType: "audio/webm",
      sizeBytes: 5,
      durationSeconds: 120,
      data: Buffer.from("audio")
    });

    const app = createApp({
      sessionService,
      authService,
      webOrigin: "http://localhost:3000"
    });
    const server = createServer(app);
    activeServers.add(server);

    await new Promise<void>((resolve) => {
      server.listen(0, "127.0.0.1", () => resolve());
    });

    const address = server.address();

    if (!address || typeof address === "string") {
      throw new Error("Expected the test server to bind to a TCP port.");
    }

    const response = await fetch(
      `http://127.0.0.1:${address.port}/sessions/${session.id}/transcription`,
      {
        method: "POST",
        headers: {
          Authorization: `Bearer ${token}`,
          "X-Idempotency-Key": "short"
        }
      }
    );

    expect(response.status).toBe(400);
    await expect(response.json()).resolves.toMatchObject({
      error: "X-Idempotency-Key must be at least 8 characters."
    });
  });

  it("does not issue a doctor session by selection alone", async () => {
    const doctorRepository = new InMemoryDoctorRepository();
    const doctor = await doctorRepository.createOrUpdateDoctor({
      name: "Dr. Rao",
      department: "Cardiology"
    });
    const authService = new AuthService(doctorRepository, "test-secret");

    const app = createApp({
      authService,
      webOrigin: "http://localhost:3000"
    });
    const server = createServer(app);
    activeServers.add(server);

    await new Promise<void>((resolve) => {
      server.listen(0, "127.0.0.1", () => resolve());
    });

    const address = server.address();

    if (!address || typeof address === "string") {
      throw new Error("Expected the test server to bind to a TCP port.");
    }

    const response = await fetch(`http://127.0.0.1:${address.port}/auth/select`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json"
      },
      body: JSON.stringify({
        doctorId: doctor.id
      })
    });

    expect(response.status).toBe(409);
    await expect(response.json()).resolves.toMatchObject({
      code: "PIN_LOGIN_REQUIRED"
    });
  });

  it("stores and reads session pdf delivery settings", async () => {
    const { authService, doctor, token } = await createAuthenticatedAuthService();
    const sessionRepository = new InMemorySessionRepository();
    const pdfDeliveryService = new PdfDeliveryService(
      new InMemoryPdfDeliveryRepository(),
      sessionRepository
    );
    const sessionService = new SessionService(
      sessionRepository,
      new MockGeminiTranscriptionAdapter(),
      undefined,
      pdfDeliveryService
    );
    const session = await sessionService.createSession(
      {
        patient: {
          name: "Anita Sharma",
          phoneNumber: "9876543210"
        }
      },
      doctor
    );

    const app = createApp({
      sessionService,
      authService,
      webOrigin: "http://localhost:3000",
      pdfDeliveryService
    });
    const server = createServer(app);
    activeServers.add(server);

    await new Promise<void>((resolve) => {
      server.listen(0, "127.0.0.1", () => resolve());
    });

    const address = server.address();

    if (!address || typeof address === "string") {
      throw new Error("Expected the test server to bind to a TCP port.");
    }

    const prepareResponse = await fetch(
      `http://127.0.0.1:${address.port}/sessions/${session.id}/pdf-delivery`,
      {
        method: "PUT",
        headers: {
          Authorization: `Bearer ${token}`,
          "Content-Type": "application/json"
        },
        body: JSON.stringify({
          patientEnabled: true,
          pharmacyEnabled: true,
          pharmacy: {
            name: "CarePlus Pharmacy",
            phoneNumber: "9988776655"
          }
        })
      }
    );
    const statusResponse = await fetch(
      `http://127.0.0.1:${address.port}/sessions/${session.id}/pdf-delivery`,
      {
        headers: {
          Authorization: `Bearer ${token}`
        }
      }
    );

    expect(prepareResponse.status).toBe(200);
    expect(statusResponse.status).toBe(200);

    const preparePayload = (await prepareResponse.json()) as {
      delivery: {
        settings: {
          pharmacyEnabled: boolean;
          pharmacy?: { name: string };
        };
      };
    };
    const statusPayload = (await statusResponse.json()) as {
      delivery: {
        settings: {
          pharmacy?: { name: string };
        };
      };
    };

    expect(preparePayload.delivery.settings.pharmacyEnabled).toBe(true);
    expect(preparePayload.delivery.settings.pharmacy?.name).toBe(
      "CarePlus Pharmacy"
    );
    expect(statusPayload.delivery.settings.pharmacy?.name).toBe(
      "CarePlus Pharmacy"
    );
  });

  it("returns 409 for pdf delivery routes when the feature is not configured", async () => {
    const { authService, doctor, token } = await createAuthenticatedAuthService();
    const sessionService = new SessionService(
      new InMemorySessionRepository(),
      new MockGeminiTranscriptionAdapter()
    );
    const session = await sessionService.createSession(
      {
        patient: {
          name: "Anita Sharma",
          phoneNumber: "9876543210"
        }
      },
      doctor
    );

    const app = createApp({
      sessionService,
      authService,
      webOrigin: "http://localhost:3000"
    });
    const server = createServer(app);
    activeServers.add(server);

    await new Promise<void>((resolve) => {
      server.listen(0, "127.0.0.1", () => resolve());
    });

    const address = server.address();

    if (!address || typeof address === "string") {
      throw new Error("Expected the test server to bind to a TCP port.");
    }

    const response = await fetch(
      `http://127.0.0.1:${address.port}/sessions/${session.id}/pdf-delivery`,
      {
        headers: {
          Authorization: `Bearer ${token}`
        }
      }
    );

    expect(response.status).toBe(409);
    await expect(response.json()).resolves.toMatchObject({
      error: "PDF delivery has not been configured for this environment."
    });
  });

  it("accepts Twilio WhatsApp delivery status callbacks", async () => {
    const updates: Array<{
      providerMessageId: string;
      providerStatus?: string;
      errorMessage?: string;
    }> = [];
    const app = createApp({
      webOrigin: "http://localhost:3000",
      pdfDeliveryService: {
        async handleProviderStatusUpdate(input) {
          updates.push(input);
        }
      }
    });
    const server = createServer(app);
    activeServers.add(server);

    await new Promise<void>((resolve) => {
      server.listen(0, "127.0.0.1", () => resolve());
    });

    const address = server.address();

    if (!address || typeof address === "string") {
      throw new Error("Expected the test server to bind to a TCP port.");
    }

    const response = await fetch(
      `http://127.0.0.1:${address.port}/webhooks/twilio/whatsapp-status`,
      {
        method: "POST",
        headers: {
          "Content-Type": "application/x-www-form-urlencoded"
        },
        body: new URLSearchParams({
          MessageSid: "SM123",
          MessageStatus: "delivered",
          ErrorMessage: ""
        })
      }
    );

    expect(response.status).toBe(204);
    expect(updates).toEqual([
      {
        providerMessageId: "SM123",
        providerStatus: "delivered",
        errorMessage: ""
      }
    ]);
  });

  it("rejects Twilio WhatsApp callbacks with an invalid signature", async () => {
    const app = createApp({
      webOrigin: "http://localhost:3000",
      pdfDeliveryService: {
        async handleProviderStatusUpdate() {}
      },
      twilioWebhookUrl: "https://transcript.fettleconnect.com/webhooks/twilio/whatsapp-status",
      twilioRequestValidator: {
        validate() {
          return false;
        }
      }
    });
    const server = createServer(app);
    activeServers.add(server);

    await new Promise<void>((resolve) => {
      server.listen(0, "127.0.0.1", () => resolve());
    });

    const address = server.address();

    if (!address || typeof address === "string") {
      throw new Error("Expected the test server to bind to a TCP port.");
    }

    const response = await fetch(
      `http://127.0.0.1:${address.port}/webhooks/twilio/whatsapp-status`,
      {
        method: "POST",
        headers: {
          "Content-Type": "application/x-www-form-urlencoded",
          "X-Twilio-Signature": "invalid-signature"
        },
        body: new URLSearchParams({
          MessageSid: "SM123",
          MessageStatus: "delivered"
        })
      }
    );

    expect(response.status).toBe(403);
  });

  it("queues a patient pdf delivery after the document exists", async () => {
    const { authService, doctor, token } = await createAuthenticatedAuthService();
    const sessionRepository = new InMemorySessionRepository();
    const pdfDeliveryService = new PdfDeliveryService(
      new InMemoryPdfDeliveryRepository(),
      sessionRepository
    );
    const sessionService = new SessionService(
      sessionRepository,
      new MockGeminiTranscriptionAdapter(),
      undefined,
      pdfDeliveryService
    );
    const session = await sessionService.createSession(
      {
        patient: {
          name: "Anita Sharma",
          phoneNumber: "9876543210"
        }
      },
      doctor
    );

    await sessionService.uploadSessionAudio(session.id, {
      fileName: "consultation.webm",
      mimeType: "audio/webm",
      sizeBytes: 5,
      durationSeconds: 120,
      data: Buffer.from("audio")
    });
    await sessionService.submitSessionTranscription(session.id);
    await sessionService.generateSessionPdf(session.id, doctor.id);

    const app = createApp({
      sessionService,
      authService,
      webOrigin: "http://localhost:3000",
      pdfDeliveryService
    });
    const server = createServer(app);
    activeServers.add(server);

    await new Promise<void>((resolve) => {
      server.listen(0, "127.0.0.1", () => resolve());
    });

    const address = server.address();

    if (!address || typeof address === "string") {
      throw new Error("Expected the test server to bind to a TCP port.");
    }

    const response = await fetch(
      `http://127.0.0.1:${address.port}/sessions/${session.id}/pdf-delivery/send`,
      {
        method: "POST",
        headers: {
          Authorization: `Bearer ${token}`,
          "Content-Type": "application/json"
        },
        body: JSON.stringify({
          patientEnabled: true,
          pharmacyEnabled: false
        })
      }
    );

    expect(response.status).toBe(200);

    const payload = (await response.json()) as {
      delivery: {
        jobs: Array<{ recipientType: string; status: string }>;
      };
    };

    expect(payload.delivery.jobs).toHaveLength(1);
    expect(payload.delivery.jobs[0]).toMatchObject({
      recipientType: "patient",
      status: "pending"
    });
  });

  it("serves public pdf delivery media when token is valid", async () => {
    const expectedPdf = Buffer.from("%PDF-1.4 mock");
    const app = createApp({
      webOrigin: "http://localhost:3000",
      pdfDeliveryService: {
        async getPublicMediaAsset(token: string) {
          if (token !== "valid-token") {
            return null;
          }

          return {
            sessionId: "session-123",
            fileName: "summary.pdf",
            mimeType: "application/pdf",
            data: expectedPdf
          };
        }
      }
    });
    const server = createServer(app);
    activeServers.add(server);

    await new Promise<void>((resolve) => {
      server.listen(0, "127.0.0.1", () => resolve());
    });

    const address = server.address();

    if (!address || typeof address === "string") {
      throw new Error("Expected the test server to bind to a TCP port.");
    }

    const response = await fetch(
      `http://127.0.0.1:${address.port}/public/pdf-delivery/media?token=valid-token`
    );

    expect(response.status).toBe(200);
    expect(response.headers.get("content-type")).toContain("application/pdf");
    expect(response.headers.get("content-length")).toBe(String(expectedPdf.length));
    const received = Buffer.from(await response.arrayBuffer());
    expect(received.equals(expectedPdf)).toBe(true);
  });

  it("returns 404 for invalid public pdf delivery media token", async () => {
    const app = createApp({
      webOrigin: "http://localhost:3000",
      pdfDeliveryService: {
        async getPublicMediaAsset() {
          return null;
        }
      }
    });
    const server = createServer(app);
    activeServers.add(server);

    await new Promise<void>((resolve) => {
      server.listen(0, "127.0.0.1", () => resolve());
    });

    const address = server.address();

    if (!address || typeof address === "string") {
      throw new Error("Expected the test server to bind to a TCP port.");
    }

    const response = await fetch(
      `http://127.0.0.1:${address.port}/public/pdf-delivery/media?token=invalid-token`
    );

    expect(response.status).toBe(404);
  });

  it("serves a public sample pdf for Twilio media template approval", async () => {
    const app = createApp({
      webOrigin: "http://localhost:3000"
    });
    const server = createServer(app);
    activeServers.add(server);

    await new Promise<void>((resolve) => {
      server.listen(0, "127.0.0.1", () => resolve());
    });

    const address = server.address();

    if (!address || typeof address === "string") {
      throw new Error("Expected the test server to bind to a TCP port.");
    }

    const response = await fetch(
      `http://127.0.0.1:${address.port}/public/pdf-delivery/template-sample.pdf`
    );

    expect(response.status).toBe(200);
    expect(response.headers.get("content-type")).toContain("application/pdf");
    expect(response.headers.get("content-disposition")).toContain(
      "medivoice-template-sample.pdf"
    );
  });

});

class AbortTranscriptionAdapter implements TranscriptionAdapter {
  readonly providerName = "abort-adapter";

  async transcribe(
    _input: TranscriptionAdapterInput
  ): Promise<TranscriptionAdapterResult> {
    throw new DOMException("The operation was aborted.", "AbortError");
  }
}
