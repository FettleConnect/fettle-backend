import { randomUUID } from "node:crypto";

import {
  MongoClient,
  type Collection,
  type Filter,
  type UpdateFilter
} from "mongodb";

import {
  type PdfDeliveryJob,
  type PdfDeliveryJobStatus,
  type PdfDeliveryRecipientType,
  type SessionPdfDeliverySettings,
  pdfDeliveryJobSchema,
  sessionPdfDeliverySettingsSchema
} from "@medivoice/shared";

export interface PdfDeliveryJobDraft {
  sessionId: string;
  recipientType: PdfDeliveryRecipientType;
  recipientName: string;
  phoneNumber: string;
}

export interface PdfDeliveryRepository {
  ensureIndexes?(): Promise<void>;
  getSettings(sessionId: string): Promise<SessionPdfDeliverySettings | null>;
  saveSettings(
    sessionId: string,
    settings: SessionPdfDeliverySettings
  ): Promise<SessionPdfDeliverySettings>;
  createJobs(drafts: PdfDeliveryJobDraft[]): Promise<PdfDeliveryJob[]>;
  listJobsForAdmin(input?: {
    sessionId?: string;
    status?: PdfDeliveryJobStatus;
    recipientType?: PdfDeliveryRecipientType;
  }): Promise<PdfDeliveryJob[]>;
  getJobById(jobId: string): Promise<PdfDeliveryJob | null>;
  listJobsBySession(sessionId: string): Promise<PdfDeliveryJob[]>;
  claimPendingJobs(limit: number): Promise<PdfDeliveryJob[]>;
  getJobByProviderMessageId(providerMessageId: string): Promise<PdfDeliveryJob | null>;
  updateJobStatus(
    jobId: string,
    status: PdfDeliveryJobStatus,
    options?: {
      provider?: string;
      providerMessageId?: string;
      lastError?: string;
      sentAt?: string;
      incrementAttemptCount?: boolean;
    }
  ): Promise<PdfDeliveryJob | null>;
}

export class InMemoryPdfDeliveryRepository implements PdfDeliveryRepository {
  private readonly settings = new Map<string, SessionPdfDeliverySettings>();
  private readonly jobs = new Map<string, PdfDeliveryJob>();

  async getSettings(
    sessionId: string
  ): Promise<SessionPdfDeliverySettings | null> {
    return this.settings.get(sessionId) ?? null;
  }

  async saveSettings(
    sessionId: string,
    settings: SessionPdfDeliverySettings
  ): Promise<SessionPdfDeliverySettings> {
    this.settings.set(sessionId, settings);
    return settings;
  }

  async createJobs(drafts: PdfDeliveryJobDraft[]): Promise<PdfDeliveryJob[]> {
    const createdAt = new Date().toISOString();

    const jobs = drafts.map((draft) => {
      const job = pdfDeliveryJobSchema.parse({
        id: randomUUID(),
        sessionId: draft.sessionId,
        recipientType: draft.recipientType,
        recipientName: draft.recipientName,
        phoneNumber: draft.phoneNumber,
        status: "pending",
        attemptCount: 0,
        createdAt,
        updatedAt: createdAt
      });
      this.jobs.set(job.id, job);
      return job;
    });

    return jobs;
  }

  async listJobsBySession(sessionId: string): Promise<PdfDeliveryJob[]> {
    return [...this.jobs.values()]
      .filter((job) => job.sessionId === sessionId)
      .sort((left, right) => right.createdAt.localeCompare(left.createdAt));
  }

  async listJobsForAdmin(input?: {
    sessionId?: string;
    status?: PdfDeliveryJobStatus;
    recipientType?: PdfDeliveryRecipientType;
  }): Promise<PdfDeliveryJob[]> {
    return [...this.jobs.values()]
      .filter((job) => (input?.sessionId ? job.sessionId === input.sessionId : true))
      .filter((job) => (input?.status ? job.status === input.status : true))
      .filter((job) =>
        input?.recipientType ? job.recipientType === input.recipientType : true
      )
      .sort((left, right) => right.createdAt.localeCompare(left.createdAt));
  }

  async getJobById(jobId: string): Promise<PdfDeliveryJob | null> {
    return this.jobs.get(jobId) ?? null;
  }

  async claimPendingJobs(limit: number): Promise<PdfDeliveryJob[]> {
    const claimed: PdfDeliveryJob[] = [];

    for (const job of [...this.jobs.values()].sort((left, right) =>
      left.createdAt.localeCompare(right.createdAt)
    )) {
      if (claimed.length >= limit) {
        break;
      }

      if (job.status !== "pending") {
        continue;
      }

      const updatedAt = new Date().toISOString();
      const processing: PdfDeliveryJob = {
        ...job,
        status: "processing",
        updatedAt,
        attemptCount: job.attemptCount + 1
      };
      this.jobs.set(job.id, processing);
      claimed.push(processing);
    }

    return claimed;
  }

  async getJobByProviderMessageId(
    providerMessageId: string
  ): Promise<PdfDeliveryJob | null> {
    return (
      [...this.jobs.values()].find(
        (job) => job.providerMessageId === providerMessageId
      ) ?? null
    );
  }

  async updateJobStatus(
    jobId: string,
    status: PdfDeliveryJobStatus,
    options: {
      provider?: string;
      providerMessageId?: string;
      lastError?: string;
      sentAt?: string;
      incrementAttemptCount?: boolean;
    } = {}
  ): Promise<PdfDeliveryJob | null> {
    const existing = this.jobs.get(jobId);

    if (!existing) {
      return null;
    }

    const updated = pdfDeliveryJobSchema.parse({
      ...existing,
      status,
      updatedAt: new Date().toISOString(),
      attemptCount: existing.attemptCount + (options.incrementAttemptCount ? 1 : 0),
      ...(options.provider ? { provider: options.provider } : {}),
      ...(options.providerMessageId
        ? { providerMessageId: options.providerMessageId }
        : {}),
      ...(options.lastError ? { lastError: options.lastError } : { lastError: undefined }),
      ...(options.sentAt ? { sentAt: options.sentAt } : {})
    });

    this.jobs.set(jobId, updated);
    return updated;
  }
}

interface SettingsDocument
  extends Omit<SessionPdfDeliverySettings, "pharmacy"> {
  sessionId: string;
  pharmacy?: SessionPdfDeliverySettings["pharmacy"] | null;
}

interface JobDocument {
  _id: string;
  sessionId: string;
  recipientType: PdfDeliveryRecipientType;
  recipientName: string;
  phoneNumber: string;
  status: PdfDeliveryJobStatus;
  attemptCount: number;
  provider?: string;
  providerMessageId?: string;
  lastError?: string;
  createdAt: string;
  updatedAt: string;
  sentAt?: string;
}

export class MongoPdfDeliveryRepository implements PdfDeliveryRepository {
  private readonly settingsCollection: Collection<SettingsDocument>;
  private readonly jobsCollection: Collection<JobDocument>;

  constructor(client: MongoClient, databaseName: string) {
    const database = client.db(databaseName);
    this.settingsCollection = database.collection<SettingsDocument>(
      "pdf_delivery_settings"
    );
    this.jobsCollection = database.collection<JobDocument>("pdf_delivery_jobs");
  }

  async ensureIndexes(): Promise<void> {
    await Promise.all([
      this.settingsCollection.createIndex({ sessionId: 1 }, { unique: true }),
      this.jobsCollection.createIndex({ sessionId: 1, createdAt: -1 }),
      this.jobsCollection.createIndex({ status: 1, createdAt: 1 }),
      this.jobsCollection.createIndex({ providerMessageId: 1 }, { sparse: true })
    ]);
  }

  async getSettings(
    sessionId: string
  ): Promise<SessionPdfDeliverySettings | null> {
    const document = await this.settingsCollection.findOne({ sessionId });

    if (!document) {
      return null;
    }

    return sessionPdfDeliverySettingsSchema.parse({
      patientEnabled: document.patientEnabled,
      pharmacyEnabled: document.pharmacyEnabled,
      ...(document.pharmacy ? { pharmacy: document.pharmacy } : {}),
      updatedAt: document.updatedAt
    });
  }

  async saveSettings(
    sessionId: string,
    settings: SessionPdfDeliverySettings
  ): Promise<SessionPdfDeliverySettings> {
    await this.settingsCollection.updateOne(
      { sessionId },
      {
        $set: {
          sessionId,
          patientEnabled: settings.patientEnabled,
          pharmacyEnabled: settings.pharmacyEnabled,
          ...(settings.pharmacy ? { pharmacy: settings.pharmacy } : { pharmacy: null }),
          updatedAt: settings.updatedAt
        }
      },
      { upsert: true }
    );

    return settings;
  }

  async createJobs(drafts: PdfDeliveryJobDraft[]): Promise<PdfDeliveryJob[]> {
    if (!drafts.length) {
      return [];
    }

    const createdAt = new Date().toISOString();
    const jobs = drafts.map((draft) =>
      pdfDeliveryJobSchema.parse({
        id: randomUUID(),
        sessionId: draft.sessionId,
        recipientType: draft.recipientType,
        recipientName: draft.recipientName,
        phoneNumber: draft.phoneNumber,
        status: "pending",
        attemptCount: 0,
        createdAt,
        updatedAt: createdAt
      })
    );

    await this.jobsCollection.insertMany(
      jobs.map((job) => ({
        _id: job.id,
        sessionId: job.sessionId,
        recipientType: job.recipientType,
        recipientName: job.recipientName,
        phoneNumber: job.phoneNumber,
        status: job.status,
        attemptCount: job.attemptCount,
        ...(job.provider ? { provider: job.provider } : {}),
        ...(job.providerMessageId ? { providerMessageId: job.providerMessageId } : {}),
        ...(job.lastError ? { lastError: job.lastError } : {}),
        createdAt: job.createdAt,
        updatedAt: job.updatedAt,
        ...(job.sentAt ? { sentAt: job.sentAt } : {})
      }))
    );

    return jobs;
  }

  async listJobsBySession(sessionId: string): Promise<PdfDeliveryJob[]> {
    const documents = await this.jobsCollection
      .find({ sessionId })
      .sort({ createdAt: -1 })
      .toArray();

    return documents.map(parseJobDocument);
  }

  async listJobsForAdmin(input?: {
    sessionId?: string;
    status?: PdfDeliveryJobStatus;
    recipientType?: PdfDeliveryRecipientType;
  }): Promise<PdfDeliveryJob[]> {
    const documents = await this.jobsCollection
      .find({
        ...(input?.sessionId ? { sessionId: input.sessionId } : {}),
        ...(input?.status ? { status: input.status } : {}),
        ...(input?.recipientType ? { recipientType: input.recipientType } : {})
      })
      .sort({ createdAt: -1 })
      .limit(500)
      .toArray();

    return documents.map(parseJobDocument);
  }

  async getJobById(jobId: string): Promise<PdfDeliveryJob | null> {
    const document = await this.jobsCollection.findOne({ _id: jobId });
    return document ? parseJobDocument(document) : null;
  }

  async claimPendingJobs(limit: number): Promise<PdfDeliveryJob[]> {
    const pending = await this.jobsCollection
      .find({ status: "pending" })
      .sort({ createdAt: 1 })
      .limit(limit)
      .toArray();
    const claimed: PdfDeliveryJob[] = [];

    for (const document of pending) {
      const updatedAt = new Date().toISOString();
      const result = await this.jobsCollection.findOneAndUpdate(
        {
          _id: document._id,
          status: "pending"
        },
        {
          $set: {
            status: "processing",
            updatedAt
          },
          $inc: {
            attemptCount: 1
          }
        },
        {
          returnDocument: "after"
        }
      );

      if (result) {
        claimed.push(parseJobDocument(result));
      }
    }

    return claimed;
  }

  async getJobByProviderMessageId(
    providerMessageId: string
  ): Promise<PdfDeliveryJob | null> {
    const document = await this.jobsCollection.findOne({ providerMessageId });
    return document ? parseJobDocument(document) : null;
  }

  async updateJobStatus(
    jobId: string,
    status: PdfDeliveryJobStatus,
    options: {
      provider?: string;
      providerMessageId?: string;
      lastError?: string;
      sentAt?: string;
      incrementAttemptCount?: boolean;
    } = {}
  ): Promise<PdfDeliveryJob | null> {
    const filter: Filter<JobDocument> = { _id: jobId };
    const update: UpdateFilter<JobDocument> = {
      $set: {
        status,
        updatedAt: new Date().toISOString(),
        ...(options.provider ? { provider: options.provider } : {}),
        ...(options.providerMessageId
          ? { providerMessageId: options.providerMessageId }
          : {}),
        ...(options.lastError ? { lastError: options.lastError } : {}),
        ...(options.sentAt ? { sentAt: options.sentAt } : {})
      },
      ...(options.incrementAttemptCount
        ? {
            $inc: {
              attemptCount: 1
            }
          }
        : {})
    };

    if (options.lastError === undefined) {
      update.$unset = {
        lastError: ""
      };
    }

    const updated = await this.jobsCollection.findOneAndUpdate(filter, update, {
      returnDocument: "after"
    });

    return updated ? parseJobDocument(updated) : null;
  }
}

function parseJobDocument(document: JobDocument): PdfDeliveryJob {
  return pdfDeliveryJobSchema.parse({
    id: document._id,
    sessionId: document.sessionId,
    recipientType: document.recipientType,
    recipientName: document.recipientName,
    phoneNumber: document.phoneNumber,
    status: document.status,
    attemptCount: document.attemptCount,
    ...(document.provider ? { provider: document.provider } : {}),
    ...(document.providerMessageId
      ? { providerMessageId: document.providerMessageId }
      : {}),
    ...(document.lastError ? { lastError: document.lastError } : {}),
    createdAt: document.createdAt,
    updatedAt: document.updatedAt,
    ...(document.sentAt ? { sentAt: document.sentAt } : {})
  });
}
