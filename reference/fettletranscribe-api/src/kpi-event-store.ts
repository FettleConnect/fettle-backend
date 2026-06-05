import { type Collection, type MongoClient } from "mongodb";

import { resolveMongoDatabaseName } from "./mongo.js";

export interface PersistedKpiEvent {
  timestamp: string;
  metric: string;
  status: "success" | "failure";
  section: string;
  elapsedMs?: number;
  errorClass?: string;
  error?: string;
  provider?: string;
  sessionId?: string;
  operation?: string;
  fallbackUsed?: boolean;
  fallbackProviderChain?: string;
}

interface KpiEventDocument extends PersistedKpiEvent {
  _id: string;
}

export interface KpiEventStore {
  append(event: PersistedKpiEvent): Promise<void>;
  listSince(since: string): Promise<PersistedKpiEvent[]>;
}

const collectionName = "kpi_events";
const defaultRetentionDays = 90;

export class MongoKpiEventStore implements KpiEventStore {
  private readonly collection: Collection<KpiEventDocument>;

  constructor(
    client: MongoClient,
    mongoUri: string,
    private readonly retentionDays = defaultRetentionDays
  ) {
    const dbName = resolveMongoDatabaseName(mongoUri);
    this.collection = client.db(dbName).collection<KpiEventDocument>(collectionName);
  }

  async ensureIndexes(): Promise<void> {
    await this.collection.createIndex({ timestamp: -1 });
    await this.collection.createIndex(
      { timestamp: 1 },
      {
        expireAfterSeconds: Math.max(1, this.retentionDays) * 24 * 60 * 60
      }
    );
  }

  async append(event: PersistedKpiEvent): Promise<void> {
    await this.collection.insertOne({
      _id: `${event.metric}:${event.timestamp}:${Math.random().toString(36).slice(2, 10)}`,
      ...event
    });
  }

  async listSince(since: string): Promise<PersistedKpiEvent[]> {
    return this.collection
      .find(
        { timestamp: { $gte: since } },
        {
          projection: {
            _id: 0
          }
        }
      )
      .sort({ timestamp: 1 })
      .toArray();
  }
}
