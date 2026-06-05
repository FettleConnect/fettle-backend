import { createHash, randomUUID } from "node:crypto";

import { type Collection, type MongoClient } from "mongodb";
import { z } from "zod";

import { type ConsultationSession } from "@medivoice/shared";

import { emitKpi } from "./kpi.js";
import { classifyError, logError, logInfo, logWarn } from "./logger.js";

const syncJobStatusSchema = z.enum([
  "queued",
  "processing",
  "succeeded",
  "dead_letter"
]);

const syncTriggerSchema = z.enum([
  "transcription_completed",
  "draft_updated",
  "dry_run"
]);

const fettleSyncPayloadSchema = z.object({
  medivoiceSessionId: z.string().trim().min(1),
  medivoiceSyncKey: z.string().trim().min(1),
  trigger: syncTriggerSchema,
  doctorEmail: z.string().trim(),
  patientName: z.string().trim().min(1),
  patientMobile: z.string().trim().min(1),
  patientEmail: z.string().trim(),
  overallSummary: z.string(),
  transcriptions: z.array(
    z.object({
      speaker: z.string().trim().min(1),
      text: z.string().trim().min(1),
      timestamp: z.number().finite().nonnegative()
    })
  ),
  metaData: z.record(z.string(), z.unknown())
});

export type FettleSyncJobStatus = z.infer<typeof syncJobStatusSchema>;
export type FettleSyncTrigger = z.infer<typeof syncTriggerSchema>;
export type FettleSyncPayload = z.infer<typeof fettleSyncPayloadSchema>;

export interface FettleSyncJob {
  id: string;
  sessionId: string;
  trigger: FettleSyncTrigger;
  idempotencyKey: string;
  status: FettleSyncJobStatus;
  payload: FettleSyncPayload;
  attempts: number;
  maxAttempts: number;
  firstQueuedAt: string;
  nextAttemptAt: string;
  updatedAt: string;
  lastError?: string;
  lastErrorClass?: string;
  finishedAt?: string;
}

export interface FettleSyncStats {
  enabled: boolean;
  queued: number;
  processing: number;
  succeeded: number;
  deadLetter: number;
  retryable: number;
  lastUpdatedAt?: string;
}

export interface FettleSyncJobSummary {
  id: string;
  sessionId: string;
  trigger: FettleSyncTrigger;
  status: FettleSyncJobStatus;
  attempts: number;
  maxAttempts: number;
  firstQueuedAt: string;
  nextAttemptAt: string;
  updatedAt: string;
  lastError?: string;
  lastErrorClass?: string;
  finishedAt?: string;
}

export interface FettleSyncRepository {
  ensureIndexes?(): Promise<void>;
  enqueue(job: FettleSyncJob): Promise<FettleSyncJob>;
  claimDueJobs(limit: number, now: string): Promise<FettleSyncJob[]>;
  recoverStaleProcessing(staleBefore: string, now: string): Promise<number>;
  markSucceeded(jobId: string, finishedAt: string): Promise<void>;
  markFailed(
    jobId: string,
    input: {
      status: "queued" | "dead_letter";
      nextAttemptAt: string;
      updatedAt: string;
      lastError: string;
      lastErrorClass: string;
      finishedAt?: string;
    }
  ): Promise<void>;
  getStats(enabled: boolean): Promise<FettleSyncStats>;
  listJobs(input?: {
    status?: FettleSyncJobStatus;
    limit?: number;
  }): Promise<FettleSyncJobSummary[]>;
  replayDeadLetter(jobId: string, nextAttemptAt: string): Promise<FettleSyncJob | null>;
}

export class InMemoryFettleSyncRepository implements FettleSyncRepository {
  private readonly jobs = new Map<string, FettleSyncJob>();

  async enqueue(job: FettleSyncJob): Promise<FettleSyncJob> {
    const existing = [...this.jobs.values()].find(
      (candidate) => candidate.idempotencyKey === job.idempotencyKey
    );
    if (existing) {
      return existing;
    }
    this.jobs.set(job.id, job);
    return job;
  }

  async claimDueJobs(limit: number, now: string): Promise<FettleSyncJob[]> {
    const due = [...this.jobs.values()]
      .filter((job) => job.status === "queued" && job.nextAttemptAt <= now)
      .sort((left, right) => left.nextAttemptAt.localeCompare(right.nextAttemptAt))
      .slice(0, limit);
    for (const job of due) {
      this.jobs.set(job.id, {
        ...job,
        status: "processing",
        attempts: job.attempts + 1,
        updatedAt: now
      });
    }
    return due.map((job) => this.jobs.get(job.id)!);
  }

  async recoverStaleProcessing(staleBefore: string, now: string): Promise<number> {
    let recovered = 0;
    for (const job of this.jobs.values()) {
      if (job.status !== "processing" || job.updatedAt > staleBefore) {
        continue;
      }
      this.jobs.set(job.id, {
        ...job,
        status: "queued",
        nextAttemptAt: now,
        updatedAt: now
      });
      recovered += 1;
    }
    return recovered;
  }

  async markSucceeded(jobId: string, finishedAt: string): Promise<void> {
    const job = this.jobs.get(jobId);
    if (!job) return;
    this.jobs.set(jobId, {
      ...job,
      status: "succeeded",
      updatedAt: finishedAt,
      finishedAt
    });
  }

  async markFailed(
    jobId: string,
    input: {
      status: "queued" | "dead_letter";
      nextAttemptAt: string;
      updatedAt: string;
      lastError: string;
      lastErrorClass: string;
      finishedAt?: string;
    }
  ): Promise<void> {
    const job = this.jobs.get(jobId);
    if (!job) return;
    this.jobs.set(jobId, {
      ...job,
      status: input.status,
      nextAttemptAt: input.nextAttemptAt,
      updatedAt: input.updatedAt,
      lastError: input.lastError,
      lastErrorClass: input.lastErrorClass,
      ...(input.finishedAt ? { finishedAt: input.finishedAt } : {})
    });
  }

  async getStats(enabled: boolean): Promise<FettleSyncStats> {
    return summarizeJobs([...this.jobs.values()], enabled);
  }

  async listJobs(input?: {
    status?: FettleSyncJobStatus;
    limit?: number;
  }): Promise<FettleSyncJobSummary[]> {
    return [...this.jobs.values()]
      .filter((job) => (input?.status ? job.status === input.status : true))
      .sort((left, right) => right.updatedAt.localeCompare(left.updatedAt))
      .slice(0, input?.limit ?? 50)
      .map(toJobSummary);
  }

  async replayDeadLetter(jobId: string, nextAttemptAt: string): Promise<FettleSyncJob | null> {
    const job = this.jobs.get(jobId);
    if (!job || job.status !== "dead_letter") return null;
    const {
      finishedAt: _finishedAt,
      lastError: _lastError,
      lastErrorClass: _lastErrorClass,
      ...rest
    } = job;
    const updated: FettleSyncJob = {
      ...rest,
      status: "queued",
      attempts: 0,
      nextAttemptAt,
      updatedAt: nextAttemptAt
    };
    this.jobs.set(jobId, updated);
    return updated;
  }
}

export class MongoFettleSyncRepository implements FettleSyncRepository {
  private readonly collection: Collection<FettleSyncJob>;

  constructor(client: MongoClient, dbName: string) {
    this.collection = client.db(dbName).collection<FettleSyncJob>("fettle_sync_jobs");
  }

  async ensureIndexes(): Promise<void> {
    await this.collection.createIndex({ idempotencyKey: 1 }, { unique: true });
    await this.collection.createIndex({ status: 1, nextAttemptAt: 1 });
    await this.collection.createIndex({ sessionId: 1, updatedAt: -1 });
  }

  async enqueue(job: FettleSyncJob): Promise<FettleSyncJob> {
    await this.collection.updateOne(
      { idempotencyKey: job.idempotencyKey },
      { $setOnInsert: job },
      { upsert: true }
    );
    return (await this.collection.findOne({ idempotencyKey: job.idempotencyKey })) ?? job;
  }

  async claimDueJobs(limit: number, now: string): Promise<FettleSyncJob[]> {
    const claimed: FettleSyncJob[] = [];
    for (let index = 0; index < limit; index += 1) {
      const result = await this.collection.findOneAndUpdate(
        {
          status: "queued",
          nextAttemptAt: { $lte: now }
        },
        {
          $set: {
            status: "processing",
            updatedAt: now
          },
          $inc: {
            attempts: 1
          }
        },
        {
          sort: { nextAttemptAt: 1 },
          returnDocument: "after"
        }
      );
      if (!result) break;
      claimed.push(result);
    }
    return claimed;
  }

  async recoverStaleProcessing(staleBefore: string, now: string): Promise<number> {
    const result = await this.collection.updateMany(
      {
        status: "processing",
        updatedAt: { $lte: staleBefore }
      },
      {
        $set: {
          status: "queued",
          nextAttemptAt: now,
          updatedAt: now
        }
      }
    );
    return result.modifiedCount;
  }

  async markSucceeded(jobId: string, finishedAt: string): Promise<void> {
    await this.collection.updateOne(
      { id: jobId },
      {
        $set: {
          status: "succeeded",
          updatedAt: finishedAt,
          finishedAt
        },
        $unset: {
          lastError: "",
          lastErrorClass: ""
        }
      }
    );
  }

  async markFailed(
    jobId: string,
    input: {
      status: "queued" | "dead_letter";
      nextAttemptAt: string;
      updatedAt: string;
      lastError: string;
      lastErrorClass: string;
      finishedAt?: string;
    }
  ): Promise<void> {
    await this.collection.updateOne(
      { id: jobId },
      {
        $set: {
          status: input.status,
          nextAttemptAt: input.nextAttemptAt,
          updatedAt: input.updatedAt,
          lastError: input.lastError,
          lastErrorClass: input.lastErrorClass,
          ...(input.finishedAt ? { finishedAt: input.finishedAt } : {})
        }
      }
    );
  }

  async getStats(enabled: boolean): Promise<FettleSyncStats> {
    return summarizeJobs(await this.collection.find({}).toArray(), enabled);
  }

  async listJobs(input?: {
    status?: FettleSyncJobStatus;
    limit?: number;
  }): Promise<FettleSyncJobSummary[]> {
    const filter = input?.status ? { status: input.status } : {};
    const jobs = await this.collection
      .find(filter)
      .sort({ updatedAt: -1 })
      .limit(input?.limit ?? 50)
      .toArray();
    return jobs.map(toJobSummary);
  }

  async replayDeadLetter(jobId: string, nextAttemptAt: string): Promise<FettleSyncJob | null> {
    return this.collection.findOneAndUpdate(
      { id: jobId, status: "dead_letter" },
      {
        $set: {
          status: "queued",
          attempts: 0,
          nextAttemptAt,
          updatedAt: nextAttemptAt
        },
        $unset: {
          finishedAt: "",
          lastError: "",
          lastErrorClass: ""
        }
      },
      { returnDocument: "after" }
    );
  }
}

export class FettleSyncService {
  constructor(
    private readonly repository: FettleSyncRepository,
    private readonly options: {
      enabled: boolean;
      backendUrl?: string;
      secret?: string;
      timeoutMs: number;
      maxAttempts: number;
      retryWindowMs: number;
      baseRetryDelayMs: number;
      staleProcessingMs: number;
      fetchImpl?: typeof fetch;
    }
  ) {}

  get enabled(): boolean {
    return this.options.enabled;
  }

  async enqueueSessionSync(
    session: ConsultationSession,
    trigger: Exclude<FettleSyncTrigger, "dry_run">
  ): Promise<FettleSyncJob | null> {
    if (!this.options.enabled) {
      return null;
    }
    const now = new Date().toISOString();
    const syncKey = buildSyncKey(session, trigger);
    const job: FettleSyncJob = {
      id: randomUUID(),
      sessionId: session.id,
      trigger,
      idempotencyKey: syncKey,
      status: "queued",
      payload: buildFettlePayload(session, trigger, syncKey),
      attempts: 0,
      maxAttempts: this.options.maxAttempts,
      firstQueuedAt: now,
      nextAttemptAt: now,
      updatedAt: now
    };

    try {
      const queued = await this.repository.enqueue(job);
      emitKpi(
        "fettle_sync.queued",
        "success",
        { sessionId: session.id, operation: trigger },
        { scope: "fettle_sync" }
      );
      return queued;
    } catch (error) {
      emitKpi(
        "fettle_sync.queued",
        "failure",
        {
          sessionId: session.id,
          operation: trigger,
          error: error instanceof Error ? error.message : "Unknown sync queue error",
          errorClass: classifyError(error)
        },
        { scope: "fettle_sync" }
      );
      logError(
        "fettle_sync_enqueue_failed",
        { sessionId: session.id, trigger },
        { scope: "fettle_sync", error }
      );
      return null;
    }
  }

  async enqueueDryRun(): Promise<FettleSyncJob> {
    const now = new Date().toISOString();
    const idempotencyKey = `dry-run:${now}:${randomUUID()}`;
    return this.repository.enqueue({
      id: randomUUID(),
      sessionId: "dry-run",
      trigger: "dry_run",
      idempotencyKey,
      status: "queued",
      payload: {
        medivoiceSessionId: "dry-run",
        medivoiceSyncKey: idempotencyKey,
        trigger: "dry_run",
        doctorEmail: "dry-run@fettleconnect.com",
        patientName: "KPI Dry Run",
        patientMobile: "0000000000",
        patientEmail: "",
        overallSummary: "Dry run payload for deployment verification.",
        transcriptions: [],
        metaData: {}
      },
      attempts: 0,
      maxAttempts: 1,
      firstQueuedAt: now,
      nextAttemptAt: now,
      updatedAt: now
    });
  }

  async processDueJobs(limit: number): Promise<{ processed: number; succeeded: number; failed: number; deadLetter: number }> {
    const now = new Date();
    const staleBefore = new Date(
      now.getTime() - this.options.staleProcessingMs
    ).toISOString();
    const recovered = await this.repository.recoverStaleProcessing(
      staleBefore,
      now.toISOString()
    );
    if (recovered > 0) {
      emitKpi(
        "fettle_sync.stale_recovery",
        "success",
        { recoveredCount: recovered },
        { scope: "fettle_sync" }
      );
      logWarn(
        "fettle_sync_stale_jobs_recovered",
        { recoveredCount: recovered },
        { scope: "fettle_sync" }
      );
    }
    const jobs = await this.repository.claimDueJobs(limit, new Date().toISOString());
    let succeeded = 0;
    let failed = 0;
    let deadLetter = 0;
    for (const job of jobs) {
      const result = await this.processJob(job);
      if (result === "succeeded") succeeded += 1;
      if (result === "failed") failed += 1;
      if (result === "dead_letter") deadLetter += 1;
    }
    return {
      processed: jobs.length,
      succeeded,
      failed,
      deadLetter
    };
  }

  async getStats(): Promise<FettleSyncStats> {
    return this.repository.getStats(this.options.enabled);
  }

  async listJobs(input?: {
    status?: FettleSyncJobStatus;
    limit?: number;
  }): Promise<FettleSyncJobSummary[]> {
    return this.repository.listJobs(input);
  }

  async replayDeadLetter(jobId: string): Promise<FettleSyncJob | null> {
    return this.repository.replayDeadLetter(jobId, new Date().toISOString());
  }

  private async processJob(job: FettleSyncJob): Promise<"succeeded" | "failed" | "dead_letter"> {
    const startedAt = Date.now();
    const isDryRun = job.trigger === "dry_run";
    if (!isDryRun) {
      emitKpi(
        "fettle_sync.attempt",
        "success",
        { sessionId: job.sessionId, operation: job.trigger },
        { scope: "fettle_sync" }
      );
    }
    try {
      if (!isDryRun) {
        await this.sendJob(job);
      } else {
        logInfo(
          "fettle_sync_dry_run_queue_only",
          { jobId: job.id },
          { scope: "fettle_sync" }
        );
      }
      const finishedAt = new Date().toISOString();
      await this.repository.markSucceeded(job.id, finishedAt);
      if (!isDryRun) {
        emitKpi(
          "fettle_sync.success",
          "success",
          {
            sessionId: job.sessionId,
            operation: job.trigger,
            elapsedMs: Date.now() - startedAt
          },
          { scope: "fettle_sync" }
        );
      }
      return "succeeded";
    } catch (error) {
      const now = new Date();
      const errorClass = classifyError(error);
      const retryWindowExpired =
        now.getTime() - new Date(job.firstQueuedAt).getTime() >=
        this.options.retryWindowMs;
      const shouldDeadLetter = job.attempts >= job.maxAttempts || retryWindowExpired;
      const status = shouldDeadLetter ? "dead_letter" : "queued";
      const nextAttemptAt = shouldDeadLetter
        ? now.toISOString()
        : new Date(now.getTime() + computeBackoffMs(this.options.baseRetryDelayMs, job.attempts)).toISOString();
      await this.repository.markFailed(job.id, {
        status,
        nextAttemptAt,
        updatedAt: now.toISOString(),
        lastError: error instanceof Error ? error.message : "Unknown sync error",
        lastErrorClass: errorClass,
        ...(shouldDeadLetter ? { finishedAt: now.toISOString() } : {})
      });
      if (!isDryRun) {
        emitKpi(
          shouldDeadLetter ? "fettle_sync.dead_letter" : "fettle_sync.failure",
          "failure",
          {
            sessionId: job.sessionId,
            operation: job.trigger,
            elapsedMs: Date.now() - startedAt,
            error: error instanceof Error ? error.message : "Unknown sync error",
            errorClass
          },
          { scope: "fettle_sync" }
        );
      }
      logWarn(
        shouldDeadLetter ? "fettle_sync_dead_lettered" : "fettle_sync_attempt_failed",
        {
          sessionId: job.sessionId,
          jobId: job.id,
          trigger: job.trigger,
          attempts: job.attempts,
          nextAttemptAt
        },
        { scope: "fettle_sync", error }
      );
      return shouldDeadLetter ? "dead_letter" : "failed";
    }
  }

  private async sendJob(job: FettleSyncJob): Promise<void> {
    if (!this.options.backendUrl || !this.options.secret) {
      throw new Error("Fettle sync is enabled but backend URL or secret is missing.");
    }
    const response = await fetchWithTimeout(
      `${this.options.backendUrl.replace(/\/$/, "")}/api/medivoice/sync/`,
      {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "X-Fettle-Secret": this.options.secret,
          "X-Idempotency-Key": job.idempotencyKey
        },
        body: JSON.stringify(job.payload)
      },
      this.options.timeoutMs,
      this.options.fetchImpl ?? fetch
    );
    if (!response.ok) {
      const body = await response.text().catch(() => "");
      throw new Error(
        `Fettle sync failed with status ${response.status}${body ? `: ${body.slice(0, 500)}` : ""}`
      );
    }
    logInfo(
      "fettle_sync_job_succeeded",
      { jobId: job.id, sessionId: job.sessionId, trigger: job.trigger },
      { scope: "fettle_sync" }
    );
  }
}

function toJobSummary(job: FettleSyncJob): FettleSyncJobSummary {
  return {
    id: job.id,
    sessionId: job.sessionId,
    trigger: job.trigger,
    status: job.status,
    attempts: job.attempts,
    maxAttempts: job.maxAttempts,
    firstQueuedAt: job.firstQueuedAt,
    nextAttemptAt: job.nextAttemptAt,
    updatedAt: job.updatedAt,
    ...(job.lastError ? { lastError: job.lastError } : {}),
    ...(job.lastErrorClass ? { lastErrorClass: job.lastErrorClass } : {}),
    ...(job.finishedAt ? { finishedAt: job.finishedAt } : {})
  };
}

function buildFettlePayload(
  session: ConsultationSession,
  trigger: Exclude<FettleSyncTrigger, "dry_run">,
  syncKey: string
): FettleSyncPayload {
  return fettleSyncPayloadSchema.parse({
    medivoiceSessionId: session.id,
    medivoiceSyncKey: syncKey,
    trigger,
    doctorEmail: session.doctor.email || "doctor@fettleconnect.com",
    patientName: session.patient.name,
    patientMobile: session.patient.phoneNumber,
    patientEmail: "",
    overallSummary: session.transcription?.draftDocument?.clinicalSummary || "",
    transcriptions:
      session.transcription?.transcriptSegments.map((segment) => ({
        speaker: normalizeFettleSpeaker(segment.speaker),
        text: segment.text,
        timestamp: segment.startedAtSeconds
      })) || [],
    metaData: {
      diagnosis: session.transcription?.draftDocument?.clinicalSummary,
      medicines: session.transcription?.draftDocument?.medications,
      revisitDate: session.transcription?.draftDocument?.revisitDate,
      testsPrescribed: session.transcription?.draftDocument?.testsPrescribed,
      nextSteps: session.transcription?.draftDocument?.nextSteps,
      cautions: session.transcription?.draftDocument?.cautions
    }
  });
}

function normalizeFettleSpeaker(speaker: string): "doctor" | "patient" {
  const normalized = speaker.trim().toLowerCase();
  if (normalized === "doctor") return "doctor";
  return "patient";
}

function buildSyncKey(
  session: ConsultationSession,
  trigger: Exclude<FettleSyncTrigger, "dry_run">
): string {
  return createHash("sha256")
    .update(`${session.id}:${trigger}:${session.updatedAt}:${JSON.stringify(session.transcription?.draftDocument ?? {})}`)
    .digest("hex");
}

function summarizeJobs(jobs: FettleSyncJob[], enabled: boolean): FettleSyncStats {
  const queued = jobs.filter((job) => job.status === "queued").length;
  const processing = jobs.filter((job) => job.status === "processing").length;
  const succeeded = jobs.filter((job) => job.status === "succeeded").length;
  const deadLetter = jobs.filter((job) => job.status === "dead_letter").length;
  const retryable = jobs.filter(
    (job) => job.status === "queued" && job.attempts > 0
  ).length;
  const lastUpdatedAt = jobs
    .map((job) => job.updatedAt)
    .filter(Boolean)
    .sort()
    .at(-1);
  return {
    enabled,
    queued,
    processing,
    succeeded,
    deadLetter,
    retryable,
    ...(lastUpdatedAt ? { lastUpdatedAt } : {})
  };
}

async function fetchWithTimeout(
  url: string,
  init: RequestInit,
  timeoutMs: number,
  fetchImpl: typeof fetch
): Promise<Response> {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), timeoutMs);
  try {
    return await fetchImpl(url, {
      ...init,
      signal: controller.signal
    });
  } finally {
    clearTimeout(timeout);
  }
}

function computeBackoffMs(baseDelayMs: number, attempts: number): number {
  const exponent = Math.max(0, attempts - 1);
  const capped = Math.min(baseDelayMs * 2 ** exponent, 30 * 60 * 1000);
  const jitter = Math.floor(Math.random() * 1000);
  return capped + jitter;
}
