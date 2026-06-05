import { describe, expect, it, vi } from "vitest";

import { type ConsultationSession } from "@medivoice/shared";

import {
  FettleSyncService,
  InMemoryFettleSyncRepository,
  type FettleSyncJob
} from "./fettle-sync-service.js";
import { getKpiSummary, resetKpiSummary } from "./kpi-registry.js";

function buildService(options?: {
  enabled?: boolean;
  fetchImpl?: typeof fetch;
  maxAttempts?: number;
  retryWindowMs?: number;
  baseRetryDelayMs?: number;
  staleProcessingMs?: number;
}) {
  const repository = new InMemoryFettleSyncRepository();
  const service = new FettleSyncService(repository, {
    enabled: options?.enabled ?? true,
    backendUrl: "https://fettle.example",
    secret: "sync-secret",
    timeoutMs: 1000,
    maxAttempts: options?.maxAttempts ?? 3,
    retryWindowMs: options?.retryWindowMs ?? 24 * 60 * 60 * 1000,
    baseRetryDelayMs: options?.baseRetryDelayMs ?? 1,
    staleProcessingMs: options?.staleProcessingMs ?? 5 * 60 * 1000,
    fetchImpl: options?.fetchImpl ?? (async () => new Response("ok", { status: 200 }))
  });
  return { repository, service };
}

function buildSession(): ConsultationSession {
  return {
    id: "316f0eda-fbb5-478e-b15f-c9f18f5bfc14",
    doctorId: "316f0eda-fbb5-478e-b15f-c9f18f5bfc13",
    patient: {
      name: "Anita Sharma",
      phoneNumber: "9876543210"
    },
    doctor: {
      name: "Dr. Rao",
      phoneNumber: "9123456789",
      email: "doctor@example.com"
    },
    status: "created",
    caseStatus: "open",
    createdAt: "2026-05-28T08:00:00.000Z",
    updatedAt: "2026-05-28T08:05:00.000Z",
    transcription: {
      status: "completed",
      provider: "gemini",
      completedAt: "2026-05-28T08:05:00.000Z",
      requestedAt: "2026-05-28T08:01:00.000Z",
      requiresRecovery: false,
      summary: "Cough improving.",
      transcriptSegments: [
        {
          id: "segment-1",
          speaker: "doctor",
          speakerLabel: "Doctor",
          text: "How are you feeling today?",
          startedAtSeconds: 0,
          endedAtSeconds: 3
        },
        {
          id: "segment-2",
          speaker: "patient",
          speakerLabel: "Patient",
          text: "The cough is better.",
          startedAtSeconds: 4,
          endedAtSeconds: 7
        }
      ],
      draftDocument: {
        title: "Consultation Note",
        chiefComplaint: "Cough",
        illnessHistory: "Cough for three days.",
        clinicalSummary: "Cough improving after medication.",
        medications: [],
        testsPrescribed: [],
        nextSteps: ["Continue hydration"],
        cautions: []
      }
    }
  } as ConsultationSession;
}

describe("FettleSyncService", () => {
  it("queues and sends a session sync with idempotency headers", async () => {
    resetKpiSummary();
    const fetchImpl = vi.fn(async () => new Response("ok", { status: 200 })) as unknown as typeof fetch;
    const { service } = buildService({ fetchImpl });

    const job = await service.enqueueSessionSync(buildSession(), "transcription_completed");
    const result = await service.processDueJobs(5);

    expect(job?.status).toBe("queued");
    expect(result).toMatchObject({ processed: 1, succeeded: 1, failed: 0, deadLetter: 0 });
    expect(fetchImpl).toHaveBeenCalledTimes(1);
    expect(fetchImpl).toHaveBeenCalledWith(
      "https://fettle.example/api/medivoice/sync/",
      expect.objectContaining({
        method: "POST",
        headers: expect.objectContaining({
          "X-Fettle-Secret": "sync-secret",
          "X-Idempotency-Key": expect.any(String)
        })
      })
    );
    expect((await service.getStats()).succeeded).toBe(1);
    expect((await service.listJobs())[0]).not.toHaveProperty("payload");
  });

  it("dead-letters failed sync jobs and allows replay", async () => {
    resetKpiSummary();
    const fetchImpl = vi.fn(async () => new Response("busy", { status: 503 })) as unknown as typeof fetch;
    const { service } = buildService({ fetchImpl, maxAttempts: 1 });

    await service.enqueueSessionSync(buildSession(), "draft_updated");
    const result = await service.processDueJobs(5);

    expect(result).toMatchObject({ processed: 1, succeeded: 0, failed: 0, deadLetter: 1 });
    const deadLetters = await service.listJobs({ status: "dead_letter" });
    expect(deadLetters).toHaveLength(1);
    expect(deadLetters[0]?.lastError).toContain("503");

    const replayed = await service.replayDeadLetter(deadLetters[0]!.id);
    expect(replayed?.status).toBe("queued");
    expect(replayed?.attempts).toBe(0);
    expect((await service.getStats()).queued).toBe(1);
    expect(getKpiSummary().metrics.some((metric) => metric.metric === "fettle_sync.dead_letter")).toBe(true);
  });

  it("normalizes non-doctor speakers for fettle payloads", async () => {
    resetKpiSummary();
    const fetchMock = vi.fn(async () => new Response("ok", { status: 200 }));
    const { service } = buildService({ fetchImpl: fetchMock as unknown as typeof fetch });
    const session = buildSession();
    session.transcription!.transcriptSegments = [
      ...session.transcription!.transcriptSegments,
      {
        id: "segment-3",
        speaker: "unknown",
        speakerLabel: "Unknown",
        text: "Additional context",
        startedAtSeconds: 8,
        endedAtSeconds: 9
      }
    ];

    await service.enqueueSessionSync(session, "draft_updated");
    await service.processDueJobs(1);

    const calls = (fetchMock as unknown as { mock: { calls: unknown[][] } }).mock.calls;
    const init = (calls[0]?.[1] ?? {}) as { body?: string };
    const payload = JSON.parse(init.body ?? "{}") as {
      transcriptions: Array<{ speaker: string }>;
    };
    expect(payload.transcriptions.every((segment) => ["doctor", "patient"].includes(segment.speaker))).toBe(true);
  });

  it("recovers stale processing jobs before claiming due work", async () => {
    resetKpiSummary();
    const fetchImpl = vi.fn(async () => new Response("ok", { status: 200 })) as unknown as typeof fetch;
    const { repository, service } = buildService({ fetchImpl, staleProcessingMs: 1000 });
    const staleJob: FettleSyncJob = {
      id: "stale-job",
      sessionId: "session-1",
      trigger: "dry_run",
      idempotencyKey: "stale-job-key",
      status: "processing",
      payload: {
        medivoiceSessionId: "dry-run",
        medivoiceSyncKey: "stale-job-key",
        trigger: "dry_run",
        doctorEmail: "dry-run@example.com",
        patientName: "Dry Run",
        patientMobile: "0000000000",
        patientEmail: "",
        overallSummary: "Dry run",
        transcriptions: [],
        metaData: {}
      },
      attempts: 1,
      maxAttempts: 3,
      firstQueuedAt: "2026-05-28T08:00:00.000Z",
      nextAttemptAt: "2026-05-28T08:00:00.000Z",
      updatedAt: "2026-05-28T08:00:00.000Z"
    };
    await repository.enqueue(staleJob);

    const result = await service.processDueJobs(5);

    expect(result.processed).toBe(1);
    expect(result.succeeded).toBe(1);
    expect((await service.getStats()).succeeded).toBe(1);
    expect(getKpiSummary().metrics.some((metric) => metric.metric === "fettle_sync.stale_recovery")).toBe(true);
  });
});
