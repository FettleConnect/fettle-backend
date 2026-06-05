import { MongoClient } from "mongodb";

const clientPromises = new Map<string, Promise<MongoClient>>();

export async function getMongoClient(uri: string): Promise<MongoClient> {
  const existingClient = clientPromises.get(uri);

  if (existingClient) {
    return existingClient;
  }

  const clientPromise = (async () => {
    const client = new MongoClient(uri, {
      ignoreUndefined: true
    });

    try {
      await client.connect();
      return client;
    } catch (error) {
      await client.close().catch(() => undefined);
      clientPromises.delete(uri);
      throw error;
    }
  })();

  clientPromises.set(uri, clientPromise);
  return clientPromise;
}

export async function closeAllMongoClients(): Promise<void> {
  const openClients = await Promise.allSettled(clientPromises.values());
  clientPromises.clear();

  await Promise.allSettled(
    openClients.map(async (result) => {
      if (result.status === "fulfilled") {
        await result.value.close();
      }
    })
  );
}

export function resolveMongoDatabaseName(
  uri: string,
  fallback = "medivoice_rewrite"
): string {
  try {
    const pathname = new URL(uri).pathname.replace(/^\/+/, "").trim();
    return pathname || fallback;
  } catch {
    return fallback;
  }
}
