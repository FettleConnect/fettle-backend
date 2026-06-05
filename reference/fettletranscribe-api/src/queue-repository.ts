import { randomBytes, randomUUID } from "node:crypto";

import {
  MongoClient,
  MongoServerError,
  type Collection
} from "mongodb";

import {
  type IntakeLink,
  type QueueEntry,
  type QueueEntryStatus,
  intakeLinkSchema,
  queueEntrySchema,
  queuePatientSchema
} from "@medivoice/shared";

export interface QueueEntryDraft {
  doctorId: string;
  intakeLinkId: string;
  intakeLabel: string;
  patient: {
    name: string;
    phoneNumber: string;
    age: number;
  };
  source: "qr" | "manual" | "import";
}

export interface QueueRepository {
  ensureIndexes(): Promise<void>;
  getOrCreateDefaultIntakeLink(input: {
    doctorId: string;
    doctorName: string;
  }): Promise<IntakeLink>;
  getPublicIntakeLinkByToken(token: string): Promise<IntakeLink | null>;
  createQueueEntry(draft: QueueEntryDraft): Promise<QueueEntry>;
  listQueueEntries(doctorId: string): Promise<QueueEntry[]>;
  listQueueEntriesForAdmin(input?: {
    doctorId?: string;
    status?: QueueEntryStatus;
    search?: string;
  }): Promise<QueueEntry[]>;
  listIntakeLinks(doctorId?: string): Promise<IntakeLink[]>;
  getIntakeLinkById(id: string): Promise<IntakeLink | null>;
  updateIntakeLink(
    id: string,
    input: {
      label?: string;
      isActive?: boolean;
    }
  ): Promise<IntakeLink | null>;
  findRecentWaitingEntryByPhone(
    doctorId: string,
    phoneNumber: string
  ): Promise<QueueEntry | null>;
  getQueueEntryById(id: string): Promise<QueueEntry | null>;
  acceptQueueEntry(
    id: string,
    sessionId: string,
    acceptedAt: string
  ): Promise<QueueEntry | null>;
  updateQueueEntryStatus(
    id: string,
    status: Extract<QueueEntryStatus, "cancelled" | "no_show">,
    updatedAt: string
  ): Promise<QueueEntry | null>;
}

const defaultIntakeLabel = "Clinic Queue";

interface IntakeLinkDocument {
  _id: string;
  doctorId: string;
  doctorName: string;
  label: string;
  token: string;
  isActive: boolean;
  createdAt: string;
  updatedAt: string;
}

interface QueueEntryDocument {
  _id: string;
  doctorId: string;
  intakeLinkId: string;
  intakeLabel: string;
  patient: {
    name: string;
    phoneNumber: string;
    age: number;
  };
  status: "waiting" | "accepted" | "in_consultation" | "completed" | "cancelled" | "no_show";
  source: "qr" | "manual" | "import";
  submittedAt: string;
  acceptedAt?: string;
  sessionId?: string;
  createdAt: string;
  updatedAt: string;
}

export class InMemoryQueueRepository implements QueueRepository {
  private readonly intakeLinks = new Map<string, IntakeLink>();
  private readonly intakeLinkByDoctorId = new Map<string, string>();
  private readonly intakeLinkByToken = new Map<string, string>();
  private readonly queueEntries = new Map<string, QueueEntry>();

  async ensureIndexes(): Promise<void> {
    return;
  }

  async getOrCreateDefaultIntakeLink(input: {
    doctorId: string;
    doctorName: string;
  }): Promise<IntakeLink> {
    const existingId = this.intakeLinkByDoctorId.get(input.doctorId);

    if (existingId) {
      const existing = this.intakeLinks.get(existingId);

      if (existing) {
        if (existing.doctorName === input.doctorName) {
          return existing;
        }

        const updated: IntakeLink = {
          ...existing,
          doctorName: input.doctorName,
          updatedAt: new Date().toISOString()
        };
        this.intakeLinks.set(existing.id, updated);
        return updated;
      }
    }

    const now = new Date().toISOString();
    const intakeLink = intakeLinkSchema.parse({
      id: randomUUID(),
      doctorId: input.doctorId,
      doctorName: input.doctorName,
      label: defaultIntakeLabel,
      token: createPublicToken(),
      isActive: true,
      createdAt: now,
      updatedAt: now
    });

    this.intakeLinks.set(intakeLink.id, intakeLink);
    this.intakeLinkByDoctorId.set(input.doctorId, intakeLink.id);
    this.intakeLinkByToken.set(intakeLink.token, intakeLink.id);

    return intakeLink;
  }

  async getPublicIntakeLinkByToken(token: string): Promise<IntakeLink | null> {
    const intakeLinkId = this.intakeLinkByToken.get(token);

    if (!intakeLinkId) {
      return null;
    }

    const intakeLink = this.intakeLinks.get(intakeLinkId) ?? null;

    return intakeLink && intakeLink.isActive ? intakeLink : null;
  }

  async createQueueEntry(draft: QueueEntryDraft): Promise<QueueEntry> {
    const now = new Date().toISOString();
    const queueEntry = queueEntrySchema.parse({
      id: randomUUID(),
      doctorId: draft.doctorId,
      intakeLinkId: draft.intakeLinkId,
      intakeLabel: draft.intakeLabel,
      patient: queuePatientSchema.parse(draft.patient),
      status: "waiting",
      source: draft.source,
      submittedAt: now,
      createdAt: now,
      updatedAt: now
    });

    this.queueEntries.set(queueEntry.id, queueEntry);
    return queueEntry;
  }

  async listQueueEntries(doctorId: string): Promise<QueueEntry[]> {
    return [...this.queueEntries.values()]
      .filter((entry) => entry.doctorId === doctorId && entry.status === "waiting")
      .sort((left, right) => left.submittedAt.localeCompare(right.submittedAt));
  }

  async listQueueEntriesForAdmin(input?: {
    doctorId?: string;
    status?: QueueEntryStatus;
    search?: string;
  }): Promise<QueueEntry[]> {
    const normalizedSearch = input?.search?.trim().toLowerCase();

    return [...this.queueEntries.values()]
      .filter((entry) => (input?.doctorId ? entry.doctorId === input.doctorId : true))
      .filter((entry) => (input?.status ? entry.status === input.status : true))
      .filter((entry) => {
        if (!normalizedSearch) {
          return true;
        }

        return (
          entry.patient.name.toLowerCase().includes(normalizedSearch) ||
          entry.patient.phoneNumber.includes(normalizedSearch)
        );
      })
      .sort((left, right) => right.createdAt.localeCompare(left.createdAt));
  }

  async listIntakeLinks(doctorId?: string): Promise<IntakeLink[]> {
    return [...this.intakeLinks.values()]
      .filter((link) => (doctorId ? link.doctorId === doctorId : true))
      .sort((left, right) => right.updatedAt.localeCompare(left.updatedAt));
  }

  async getIntakeLinkById(id: string): Promise<IntakeLink | null> {
    return this.intakeLinks.get(id) ?? null;
  }

  async updateIntakeLink(
    id: string,
    input: {
      label?: string;
      isActive?: boolean;
    }
  ): Promise<IntakeLink | null> {
    const existing = this.intakeLinks.get(id);

    if (!existing) {
      return null;
    }

    const updated: IntakeLink = {
      ...existing,
      ...(typeof input.label === "string" ? { label: input.label } : {}),
      ...(typeof input.isActive === "boolean"
        ? { isActive: input.isActive }
        : {}),
      updatedAt: new Date().toISOString()
    };

    this.intakeLinks.set(id, updated);
    return updated;
  }

  async findRecentWaitingEntryByPhone(
    doctorId: string,
    phoneNumber: string
  ): Promise<QueueEntry | null> {
    const entries = [...this.queueEntries.values()]
      .filter(
        (entry) =>
          entry.doctorId === doctorId &&
          entry.status === "waiting" &&
          entry.patient.phoneNumber === phoneNumber
      )
      .sort((left, right) => right.submittedAt.localeCompare(left.submittedAt));

    return entries[0] ?? null;
  }

  async getQueueEntryById(id: string): Promise<QueueEntry | null> {
    return this.queueEntries.get(id) ?? null;
  }

  async acceptQueueEntry(
    id: string,
    sessionId: string,
    acceptedAt: string
  ): Promise<QueueEntry | null> {
    const existing = this.queueEntries.get(id);

    if (!existing) {
      return null;
    }

    if (existing.status !== "waiting") {
      return existing;
    }

    const nextEntry = queueEntrySchema.parse({
      ...existing,
      status: "accepted",
      acceptedAt,
      sessionId,
      updatedAt: acceptedAt
    });

    this.queueEntries.set(id, nextEntry);
    return nextEntry;
  }

  async updateQueueEntryStatus(
    id: string,
    status: Extract<QueueEntryStatus, "cancelled" | "no_show">,
    updatedAt: string
  ): Promise<QueueEntry | null> {
    const existing = this.queueEntries.get(id);

    if (!existing) {
      return null;
    }

    if (existing.status !== "waiting") {
      return null;
    }

    const nextEntry = queueEntrySchema.parse({
      ...existing,
      status,
      updatedAt
    });

    this.queueEntries.set(id, nextEntry);
    return nextEntry;
  }
}

export class MongoQueueRepository implements QueueRepository {
  private readonly intakeLinksCollection: Collection<IntakeLinkDocument>;
  private readonly queueEntriesCollection: Collection<QueueEntryDocument>;

  constructor(client: MongoClient, dbName = "medivoice_rewrite") {
    const database = client.db(dbName);
    this.intakeLinksCollection =
      database.collection<IntakeLinkDocument>("intake_links");
    this.queueEntriesCollection =
      database.collection<QueueEntryDocument>("queue_entries");
  }

  async ensureIndexes(): Promise<void> {
    await this.intakeLinksCollection.createIndex({ doctorId: 1 }, { unique: true });
    await this.intakeLinksCollection.createIndex({ token: 1 }, { unique: true });
    await this.queueEntriesCollection.createIndex({
      doctorId: 1,
      status: 1,
      submittedAt: 1
    });
    await this.queueEntriesCollection.createIndex({
      doctorId: 1,
      "patient.phoneNumber": 1,
      status: 1,
      submittedAt: -1
    });
    await this.queueEntriesCollection.createIndex(
      {
        doctorId: 1,
        "patient.phoneNumber": 1
      },
      {
        unique: true,
        partialFilterExpression: {
          status: "waiting"
        }
      }
    );
  }

  async getOrCreateDefaultIntakeLink(input: {
    doctorId: string;
    doctorName: string;
  }): Promise<IntakeLink> {
    const existing = await this.intakeLinksCollection.findOne({
      doctorId: input.doctorId
    });

    if (existing) {
      if (existing.doctorName === input.doctorName) {
        return parseIntakeLinkDocument(existing);
      }

      const updatedAt = new Date().toISOString();
      await this.intakeLinksCollection.updateOne(
        { _id: existing._id },
        {
          $set: {
            doctorName: input.doctorName,
            updatedAt
          }
        }
      );

      return parseIntakeLinkDocument({
        ...existing,
        doctorName: input.doctorName,
        updatedAt
      });
    }

    const now = new Date().toISOString();
    const document: IntakeLinkDocument = {
      _id: randomUUID(),
      doctorId: input.doctorId,
      doctorName: input.doctorName,
      label: defaultIntakeLabel,
      token: createPublicToken(),
      isActive: true,
      createdAt: now,
      updatedAt: now
    };

    try {
      await this.intakeLinksCollection.insertOne(document);
      return parseIntakeLinkDocument(document);
    } catch (error) {
      if (isDuplicateKeyError(error)) {
        const racedDocument = await this.intakeLinksCollection.findOne({
          doctorId: input.doctorId
        });

        if (racedDocument) {
          return parseIntakeLinkDocument(racedDocument);
        }
      }

      throw error;
    }
  }

  async getPublicIntakeLinkByToken(token: string): Promise<IntakeLink | null> {
    const document = await this.intakeLinksCollection.findOne({
      token,
      isActive: true
    });

    return document ? parseIntakeLinkDocument(document) : null;
  }

  async createQueueEntry(draft: QueueEntryDraft): Promise<QueueEntry> {
    const now = new Date().toISOString();
    const document: QueueEntryDocument = {
      _id: randomUUID(),
      doctorId: draft.doctorId,
      intakeLinkId: draft.intakeLinkId,
      intakeLabel: draft.intakeLabel,
      patient: queuePatientSchema.parse(draft.patient),
      status: "waiting",
      source: draft.source,
      submittedAt: now,
      createdAt: now,
      updatedAt: now
    };

    try {
      await this.queueEntriesCollection.insertOne(document);
      return parseQueueEntryDocument(document);
    } catch (error) {
      if (isDuplicateKeyError(error)) {
        const existingWaitingEntry = await this.findRecentWaitingEntryByPhone(
          draft.doctorId,
          draft.patient.phoneNumber
        );

        if (existingWaitingEntry) {
          return existingWaitingEntry;
        }
      }

      throw error;
    }
  }

  async listQueueEntries(doctorId: string): Promise<QueueEntry[]> {
    const documents = await this.queueEntriesCollection
      .find({ doctorId, status: "waiting" })
      .sort({ submittedAt: 1 })
      .toArray();

    return documents.map(parseQueueEntryDocument);
  }

  async listQueueEntriesForAdmin(input?: {
    doctorId?: string;
    status?: QueueEntryStatus;
    search?: string;
  }): Promise<QueueEntry[]> {
    const normalizedSearch = input?.search?.trim();
    const escapedSearch = normalizedSearch
      ? `^${escapeRegex(normalizedSearch)}`
      : undefined;

    const query = {
      ...(input?.doctorId ? { doctorId: input.doctorId } : {}),
      ...(input?.status ? { status: input.status } : {}),
      ...(escapedSearch
        ? {
            $or: [
              {
                "patient.name": {
                  $regex: escapedSearch,
                  $options: "i"
                }
              },
              {
                "patient.phoneNumber": {
                  $regex: escapedSearch,
                  $options: "i"
                }
              }
            ]
          }
        : {})
    };
    const documents = await this.queueEntriesCollection
      .find(query)
      .sort({ createdAt: -1 })
      .limit(500)
      .toArray();

    return documents.map(parseQueueEntryDocument);
  }

  async listIntakeLinks(doctorId?: string): Promise<IntakeLink[]> {
    const documents = await this.intakeLinksCollection
      .find(doctorId ? { doctorId } : {})
      .sort({ updatedAt: -1 })
      .limit(500)
      .toArray();

    return documents.map(parseIntakeLinkDocument);
  }

  async getIntakeLinkById(id: string): Promise<IntakeLink | null> {
    const document = await this.intakeLinksCollection.findOne({ _id: id });
    return document ? parseIntakeLinkDocument(document) : null;
  }

  async updateIntakeLink(
    id: string,
    input: {
      label?: string;
      isActive?: boolean;
    }
  ): Promise<IntakeLink | null> {
    const updatedAt = new Date().toISOString();
    const updated = await this.intakeLinksCollection.findOneAndUpdate(
      { _id: id },
      {
        $set: {
          ...(typeof input.label === "string" ? { label: input.label } : {}),
          ...(typeof input.isActive === "boolean"
            ? { isActive: input.isActive }
            : {}),
          updatedAt
        }
      },
      {
        returnDocument: "after"
      }
    );

    return updated ? parseIntakeLinkDocument(updated) : null;
  }

  async findRecentWaitingEntryByPhone(
    doctorId: string,
    phoneNumber: string
  ): Promise<QueueEntry | null> {
    const document = await this.queueEntriesCollection.findOne(
      {
        doctorId,
        "patient.phoneNumber": phoneNumber,
        status: "waiting"
      },
      {
        sort: {
          submittedAt: -1
        }
      }
    );

    return document ? parseQueueEntryDocument(document) : null;
  }

  async getQueueEntryById(id: string): Promise<QueueEntry | null> {
    const document = await this.queueEntriesCollection.findOne({ _id: id });
    return document ? parseQueueEntryDocument(document) : null;
  }

  async acceptQueueEntry(
    id: string,
    sessionId: string,
    acceptedAt: string
  ): Promise<QueueEntry | null> {
    const updated = await this.queueEntriesCollection.findOneAndUpdate(
      {
        _id: id,
        status: "waiting"
      },
      {
        $set: {
          status: "accepted",
          acceptedAt,
          sessionId,
          updatedAt: acceptedAt
        }
      },
      {
        returnDocument: "after"
      }
    );

    if (updated) {
      return parseQueueEntryDocument(updated);
    }

    return this.getQueueEntryById(id);
  }

  async updateQueueEntryStatus(
    id: string,
    status: Extract<QueueEntryStatus, "cancelled" | "no_show">,
    updatedAt: string
  ): Promise<QueueEntry | null> {
    const updated = await this.queueEntriesCollection.findOneAndUpdate(
      {
        _id: id,
        status: "waiting"
      },
      {
        $set: {
          status,
          updatedAt
        }
      },
      {
        returnDocument: "after"
      }
    );

    if (updated) {
      return parseQueueEntryDocument(updated);
    }

    return null;
  }
}

function createPublicToken(): string {
  return randomBytes(18).toString("base64url");
}

function parseIntakeLinkDocument(document: IntakeLinkDocument): IntakeLink {
  return intakeLinkSchema.parse({
    id: document._id,
    doctorId: document.doctorId,
    doctorName: document.doctorName,
    label: document.label,
    token: document.token,
    isActive: document.isActive,
    createdAt: document.createdAt,
    updatedAt: document.updatedAt
  });
}

function parseQueueEntryDocument(document: QueueEntryDocument): QueueEntry {
  return queueEntrySchema.parse({
    id: document._id,
    doctorId: document.doctorId,
    intakeLinkId: document.intakeLinkId,
    intakeLabel: document.intakeLabel,
    patient: document.patient,
    status: document.status,
    source: document.source,
    submittedAt: document.submittedAt,
    acceptedAt: document.acceptedAt,
    sessionId: document.sessionId,
    createdAt: document.createdAt,
    updatedAt: document.updatedAt
  });
}

function isDuplicateKeyError(error: unknown): error is MongoServerError {
  return error instanceof MongoServerError && error.code === 11000;
}

function escapeRegex(value: string): string {
  return value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}
