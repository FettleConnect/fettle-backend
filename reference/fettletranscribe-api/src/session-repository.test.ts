import { Binary, MongoClient } from "mongodb";
import { describe, expect, it, vi } from "vitest";

import { MongoSessionRepository, parseSessionDocument } from "./session-repository.js";

type SessionDocument = {
  _id: string;
  doctorId: string;
  patient: { name: string; phoneNumber: string };
  doctor: { name: string; phoneNumber?: string | undefined };
  status: "created";
  createdAt: string;
  updatedAt: string;
};

type AudioCollectionUpdate = {
  filter: { sessionId: string };
  update: {
    $set: {
      sessionId: string;
      fileName: string;
      mimeType: string;
      sizeBytes: number;
      durationSeconds: number;
      uploadedAt: string;
      data: Binary;
    };
    $setOnInsert: {
      _id: string;
    };
  };
  options: { upsert: true; session?: unknown };
};

type DocumentCollectionUpdate = {
  filter: { sessionId: string };
  update: {
    $set: {
      sessionId: string;
      fileName: string;
      mimeType: string;
      sizeBytes: number;
      createdAt: string;
      delivery: {
        shareTitle: string;
        shareMessage: string;
        whatsappMessage: string;
      };
      html: string;
      pdf: Binary;
    };
    $setOnInsert: {
      _id: string;
    };
  };
  options: { upsert: true };
};

describe("MongoSessionRepository", () => {
  it("uses a deterministic audio document id and writes inside a transaction", async () => {
    const sessionId = "316f0eda-fbb5-478e-b15f-c9f18f5bfc14";
    const existingSession: SessionDocument = {
      _id: sessionId,
      doctorId: "316f0eda-fbb5-478e-b15f-c9f18f5bfc13",
      patient: { name: "Anita Sharma", phoneNumber: "9876543210" },
      doctor: { name: "Dr. Rao" },
      status: "created",
      createdAt: "2026-03-18T00:00:00.000Z",
      updatedAt: "2026-03-18T00:00:00.000Z"
    };

    const sessionCollection = {
      findOne: vi.fn(async (_filter, options) => {
        expect(options).toMatchObject({ session: expect.any(Object) });
        return existingSession;
      }),
      updateOne: vi.fn(async () => ({ acknowledged: true })),
      insertOne: vi.fn(),
      createIndex: vi.fn(),
      find: vi.fn()
    };
    const audioUpdates: AudioCollectionUpdate[] = [];
    const audioCollection = {
      updateOne: vi.fn(async (filter, update, options) => {
        audioUpdates.push({
          filter: filter as AudioCollectionUpdate["filter"],
          update: update as AudioCollectionUpdate["update"],
          options: options as AudioCollectionUpdate["options"]
        });

        return { acknowledged: true };
      }),
      createIndex: vi.fn()
    };
    const transactionSession = {
      withTransaction: vi.fn(async (callback: () => Promise<unknown>) => callback()),
      endSession: vi.fn(async () => undefined)
    };
    const database = {
      collection: vi.fn((name: string) =>
        name === "sessions" ? sessionCollection : audioCollection
      )
    };
    const client = {
      db: vi.fn(() => database),
      startSession: vi.fn(() => transactionSession)
    };

    const repository = new MongoSessionRepository(
      client as unknown as MongoClient,
      "medivoice_rewrite"
    );

    const updatedSession = await repository.uploadSessionAudio(sessionId, {
      fileName: "consultation.webm",
      mimeType: "audio/webm",
      sizeBytes: 8,
      durationSeconds: 12.5,
      data: Buffer.from("new-audio")
    });

    expect(updatedSession?.audioArtifact?.fileName).toBe("consultation.webm");
    expect(audioUpdates).toHaveLength(1);
    expect(updatedSession?.audioArtifact?.id).toBe(sessionId);
    expect(audioUpdates[0]?.update.$setOnInsert._id).toBe(sessionId);
    expect(audioUpdates[0]?.update.$set.data).toBeInstanceOf(Binary);
    expect(audioUpdates[0]?.options).toMatchObject({
      upsert: true,
      session: transactionSession
    });
    expect(transactionSession.withTransaction).toHaveBeenCalledTimes(1);
    expect(transactionSession.endSession).toHaveBeenCalledTimes(1);
  });

  it("falls back cleanly when transactions are unsupported", async () => {
    const sessionId = "316f0eda-fbb5-478e-b15f-c9f18f5bfc14";
    const existingSession: SessionDocument = {
      _id: sessionId,
      doctorId: "316f0eda-fbb5-478e-b15f-c9f18f5bfc13",
      patient: { name: "Anita Sharma", phoneNumber: "9876543210" },
      doctor: { name: "Dr. Rao" },
      status: "created",
      createdAt: "2026-03-18T00:00:00.000Z",
      updatedAt: "2026-03-18T00:00:00.000Z"
    };
    const sessionCollection = {
      findOne: vi.fn(async () => existingSession),
      findOneAndUpdate: vi.fn(async () => ({
        ...existingSession,
        transcription: {
          status: "processing",
          provider: "mock-gemini",
          requestedAt: "2026-03-18T00:02:00.000Z",
          transcriptSegments: []
        },
        updatedAt: "2026-03-18T00:02:00.000Z"
      })),
      updateOne: vi.fn(async () => ({ acknowledged: true })),
      insertOne: vi.fn(),
      createIndex: vi.fn(),
      find: vi.fn()
    };
    const audioCollection = {
      updateOne: vi.fn(async () => ({ acknowledged: true })),
      createIndex: vi.fn()
    };
    const transactionSession = {
      withTransaction: vi.fn(async () => {
        throw {
          name: "MongoServerError",
          code: 20,
          codeName: "IllegalOperation",
          message:
            "Transaction numbers are only allowed on a replica set member or mongos"
        };
      }),
      endSession: vi.fn(async () => undefined)
    };
    const database = {
      collection: vi.fn((name: string) =>
        name === "sessions" ? sessionCollection : audioCollection
      )
    };
    const client = {
      db: vi.fn(() => database),
      startSession: vi.fn(() => transactionSession)
    };

    const repository = new MongoSessionRepository(
      client as unknown as MongoClient,
      "medivoice_rewrite"
    );

    const updatedSession = await repository.uploadSessionAudio(sessionId, {
      fileName: "consultation.webm",
      mimeType: "audio/webm",
      sizeBytes: 8,
      durationSeconds: 12.5,
      data: Buffer.from("new-audio")
    });

    expect(updatedSession?.audioArtifact?.id).toBe(sessionId);
    expect(audioCollection.updateOne).toHaveBeenCalledTimes(1);
    expect(sessionCollection.updateOne).toHaveBeenCalledTimes(1);
    expect(transactionSession.endSession).toHaveBeenCalledTimes(1);
  });

  it("preserves existing transcript segments when marking transcription as processing", async () => {
    const sessionId = "316f0eda-fbb5-478e-b15f-c9f18f5bfc14";
    const existingSession: SessionDocument & {
      transcription: {
        status: "completed";
        provider: string;
        requestedAt: string;
        completedAt: string;
        transcriptSegments: Array<{
          id: string;
          speaker: "doctor";
          speakerLabel: string;
          text: string;
          startedAtSeconds: number;
          endedAtSeconds: number;
        }>;
        summary: string;
      };
    } = {
      _id: sessionId,
      doctorId: "316f0eda-fbb5-478e-b15f-c9f18f5bfc13",
      patient: { name: "Anita Sharma", phoneNumber: "9876543210" },
      doctor: { name: "Dr. Rao" },
      status: "created",
      transcription: {
        status: "completed",
        provider: "mock-gemini",
        requestedAt: "2026-03-18T00:00:00.000Z",
        completedAt: "2026-03-18T00:01:00.000Z",
        transcriptSegments: [
          {
            id: "11111111-1111-4111-8111-111111111111",
            speaker: "doctor",
            speakerLabel: "Dr. Rao",
            text: "How are you feeling today?",
            startedAtSeconds: 0,
            endedAtSeconds: 4
          }
        ],
        summary: "Existing summary"
      },
      createdAt: "2026-03-18T00:00:00.000Z",
      updatedAt: "2026-03-18T00:01:00.000Z"
    };
    const sessionCollection = {
      findOne: vi.fn(async () => existingSession),
      findOneAndUpdate: vi.fn(async () => ({
        ...existingSession,
        transcription: {
          status: "processing",
          provider: "mock-gemini",
          requestedAt: "2026-03-18T00:02:00.000Z",
          transcriptSegments: existingSession.transcription.transcriptSegments
        },
        updatedAt: "2026-03-18T00:02:00.000Z"
      })),
      updateOne: vi.fn(async () => ({ acknowledged: true })),
      insertOne: vi.fn(),
      createIndex: vi.fn(),
      find: vi.fn()
    };
    const database = {
      collection: vi.fn(() => sessionCollection)
    };
    const client = {
      db: vi.fn(() => database),
      startSession: vi.fn()
    };

    const repository = new MongoSessionRepository(
      client as unknown as MongoClient,
      "medivoice_rewrite"
    );

    const updatedSession = await repository.markTranscriptionProcessing(
      sessionId,
      "mock-gemini",
      "2026-03-18T00:02:00.000Z"
    );

    expect(updatedSession?.transcription?.status).toBe("processing");
    expect(updatedSession?.transcription?.transcriptSegments).toHaveLength(1);
    expect(updatedSession?.transcription?.transcriptSegments[0]?.text).toBe(
      "How are you feeling today?"
    );
    expect(updatedSession?.transcription?.requestedAt).toBe(
      "2026-03-18T00:02:00.000Z"
    );
    expect(sessionCollection.findOneAndUpdate).toHaveBeenCalledTimes(1);
    const firstCall = sessionCollection.findOneAndUpdate.mock.calls[0] as
      | [unknown, { $unset?: Record<string, string> }]
      | undefined;
    expect(firstCall).toBeDefined();
    const [, updateDocument] = firstCall!;
    expect(updateDocument.$unset).toBeDefined();
    expect(updateDocument.$unset).not.toHaveProperty("transcription.stage");
  });

  it("clears stale failure details when transcription completes after a retry", async () => {
    const sessionId = "316f0eda-fbb5-478e-b15f-c9f18f5bfc14";
    const existingSession: SessionDocument & {
      transcription: {
        status: "failed";
        provider: string;
        requestedAt: string;
        failedAt: string;
        error: string;
        transcriptSegments: [];
      };
    } = {
      _id: sessionId,
      doctorId: "316f0eda-fbb5-478e-b15f-c9f18f5bfc13",
      patient: { name: "Anita Sharma", phoneNumber: "9876543210" },
      doctor: { name: "Dr. Rao" },
      status: "created",
      transcription: {
        status: "failed",
        provider: "gemini-2.5-flash",
        requestedAt: "2026-03-18T00:00:00.000Z",
        failedAt: "2026-03-18T00:01:00.000Z",
        error: "Transcription was canceled before completion.",
        transcriptSegments: []
      },
      createdAt: "2026-03-18T00:00:00.000Z",
      updatedAt: "2026-03-18T00:01:00.000Z"
    };
    const sessionCollection = {
      findOne: vi.fn(async () => existingSession),
      updateOne: vi.fn(async () => ({ acknowledged: true })),
      insertOne: vi.fn(),
      createIndex: vi.fn(),
      find: vi.fn()
    };
    const database = {
      collection: vi.fn(() => sessionCollection)
    };
    const client = {
      db: vi.fn(() => database),
      startSession: vi.fn()
    };

    const repository = new MongoSessionRepository(
      client as unknown as MongoClient,
      "medivoice_rewrite"
    );

    const updatedSession = await repository.completeTranscription(sessionId, {
      provider: "gemini-2.5-flash",
      requiresRecovery: false,
      summary: "Updated summary",
      transcriptSegments: [
        {
          id: "11111111-1111-4111-8111-111111111111",
          speaker: "doctor",
          speakerLabel: "Dr. Rao",
          text: "Let us continue.",
          startedAtSeconds: 0,
          endedAtSeconds: 2
        }
      ],
      metadata: {
        symptoms: [],
        diagnoses: [],
        careTeamNotes: []
      },
      draftDocument: {
        title: "Consultation Draft",
        clinicalSummary: "Summary",
        medications: [],
        testsPrescribed: [],
        nextSteps: [],
        cautions: []
      }
    });

    expect(updatedSession?.transcription?.status).toBe("completed");
    expect(updatedSession?.transcription?.error).toBeUndefined();
    expect(updatedSession?.transcription?.failedAt).toBeUndefined();
    expect(updatedSession?.transcription?.requestedAt).toBeUndefined();
    expect(updatedSession?.transcription?.completedAt).toBeDefined();
    expect(sessionCollection.updateOne).toHaveBeenCalledWith(
      { _id: sessionId },
      expect.objectContaining({
        $set: expect.objectContaining({
          "transcription.status": "completed"
        }),
        $unset: expect.objectContaining({
          "transcription.requestedAt": "",
          "transcription.failedAt": "",
          "transcription.error": ""
        })
      })
    );
  });

  it("blocks stale completion writes when the request key no longer matches", async () => {
    const sessionId = "316f0eda-fbb5-478e-b15f-c9f18f5bfc14";
    const sessionCollection = {
      findOne: vi.fn(async (filter: Record<string, unknown>) => {
        if (filter["transcription.requestKey"] === "req-old") {
          return null;
        }
        return {
          _id: sessionId,
          doctorId: "316f0eda-fbb5-478e-b15f-c9f18f5bfc13",
          patient: { name: "Anita Sharma", phoneNumber: "9876543210" },
          doctor: { name: "Dr. Rao" },
          status: "created",
          transcription: {
            status: "processing",
            provider: "gemini-2.5-flash",
            requestedAt: "2026-03-18T00:00:00.000Z",
            requestKey: "req-new",
            transcriptSegments: []
          },
          createdAt: "2026-03-18T00:00:00.000Z",
          updatedAt: "2026-03-18T00:00:00.000Z"
        };
      }),
      updateOne: vi.fn(async () => ({ acknowledged: true })),
      insertOne: vi.fn(),
      createIndex: vi.fn(),
      find: vi.fn()
    };
    const database = {
      collection: vi.fn(() => sessionCollection)
    };
    const client = {
      db: vi.fn(() => database),
      startSession: vi.fn()
    };
    const repository = new MongoSessionRepository(
      client as unknown as MongoClient,
      "medivoice_rewrite"
    );

    const updatedSession = await repository.completeTranscription(
      sessionId,
      {
        provider: "gemini-2.5-flash",
        requiresRecovery: false,
        summary: "Updated summary",
        transcriptSegments: [],
        metadata: {
          symptoms: [],
          diagnoses: [],
          careTeamNotes: []
        },
        draftDocument: {
          title: "Consultation Draft",
          clinicalSummary: "Summary",
          medications: [],
          testsPrescribed: [],
          nextSteps: [],
          cautions: []
        }
      },
      "req-old"
    );

    expect(updatedSession).toBeNull();
    expect(sessionCollection.updateOne).not.toHaveBeenCalled();
  });

  it("stores regenerated pdf metadata without rewriting the immutable mongo _id", async () => {
    const sessionId = "316f0eda-fbb5-478e-b15f-c9f18f5bfc14";
    const existingSession: SessionDocument & {
      documentArtifact?: {
        id: string;
        fileName: string;
        mimeType: string;
        sizeBytes: number;
        createdAt: string;
        delivery: {
          shareTitle: string;
          shareMessage: string;
          whatsappMessage: string;
        };
      };
    } = {
      _id: sessionId,
      doctorId: "316f0eda-fbb5-478e-b15f-c9f18f5bfc13",
      patient: { name: "Anita Sharma", phoneNumber: "9876543210" },
      doctor: { name: "Dr. Rao" },
      status: "created",
      documentArtifact: {
        id: "11111111-1111-4111-8111-111111111111",
        fileName: "existing.pdf",
        mimeType: "application/pdf",
        sizeBytes: 512,
        createdAt: "2026-03-18T00:00:00.000Z",
        delivery: {
          shareTitle: "Consultation Summary",
          shareMessage: "Your consultation summary is ready.",
          whatsappMessage: "Your consultation summary is ready."
        }
      },
      createdAt: "2026-03-18T00:00:00.000Z",
      updatedAt: "2026-03-18T00:00:00.000Z"
    };
    const sessionCollection = {
      findOne: vi.fn(async () => existingSession),
      updateOne: vi.fn(async () => ({ acknowledged: true })),
      insertOne: vi.fn(),
      createIndex: vi.fn(),
      find: vi.fn()
    };
    const documentUpdates: DocumentCollectionUpdate[] = [];
    const documentCollection = {
      updateOne: vi.fn(async (filter, update, options) => {
        documentUpdates.push({
          filter: filter as DocumentCollectionUpdate["filter"],
          update: update as DocumentCollectionUpdate["update"],
          options: options as DocumentCollectionUpdate["options"]
        });

        return { acknowledged: true };
      }),
      createIndex: vi.fn()
    };
    const database = {
      collection: vi.fn((name: string) =>
        name === "sessions" ? sessionCollection : documentCollection
      )
    };
    const client = {
      db: vi.fn(() => database),
      startSession: vi.fn()
    };

    const repository = new MongoSessionRepository(
      client as unknown as MongoClient,
      "medivoice_rewrite"
    );

    const updatedSession = await repository.storeGeneratedDocument(sessionId, {
      artifact: {
        id: "22222222-2222-4222-8222-222222222222",
        fileName: "updated.pdf",
        mimeType: "application/pdf",
        sizeBytes: 1024,
        createdAt: "2026-03-18T01:00:00.000Z",
        delivery: {
          shareTitle: "Consultation Summary",
          shareMessage: "Updated consultation summary is ready.",
          whatsappMessage: "Updated consultation summary is ready."
        }
      },
      html: "<html><body>Updated</body></html>",
      pdf: Buffer.from("%PDF-1.4\n")
    });

    expect(updatedSession?.documentArtifact?.id).toBe(
      "22222222-2222-4222-8222-222222222222"
    );
    expect(documentUpdates).toHaveLength(1);
    expect(documentUpdates[0]?.filter).toEqual({ sessionId });
    expect(documentUpdates[0]?.update.$setOnInsert._id).toBe(
      "22222222-2222-4222-8222-222222222222"
    );
    expect(documentUpdates[0]?.update.$set).not.toHaveProperty("_id");
    expect(documentUpdates[0]?.update.$set.pdf).toBeInstanceOf(Binary);
    expect(documentUpdates[0]?.options).toEqual({ upsert: true });
  });

  it("marks interrupted processing sessions as failed during recovery", async () => {
    const sessionCollection = {
      updateMany: vi.fn(async () => ({ modifiedCount: 2 })),
      findOne: vi.fn(),
      updateOne: vi.fn(),
      insertOne: vi.fn(),
      createIndex: vi.fn(),
      find: vi.fn()
    };
    const database = {
      collection: vi.fn(() => sessionCollection)
    };
    const client = {
      db: vi.fn(() => database),
      startSession: vi.fn()
    };

    const repository = new MongoSessionRepository(
      client as unknown as MongoClient,
      "medivoice_rewrite"
    );

    const recoveredCount = await repository.recoverInterruptedTranscriptions();

    expect(recoveredCount).toBe(2);
    expect(sessionCollection.updateMany).toHaveBeenCalledWith(
      { "transcription.status": "processing" },
      expect.objectContaining({
        $set: expect.objectContaining({
          "transcription.status": "failed",
          "transcription.error":
            "Transcription stopped when the server restarted. Please retry."
        })
      })
    );
  });

  it("recovers stale processing transcriptions after threshold", async () => {
    const sessionCollection = {
      updateMany: vi.fn(async () => ({ modifiedCount: 1 })),
      findOne: vi.fn(),
      updateOne: vi.fn(),
      insertOne: vi.fn(),
      createIndex: vi.fn(),
      find: vi.fn()
    };
    const database = {
      collection: vi.fn(() => sessionCollection)
    };
    const client = {
      db: vi.fn(() => database),
      startSession: vi.fn()
    };

    const repository = new MongoSessionRepository(
      client as unknown as MongoClient,
      "medivoice_rewrite"
    );

    const recoveredCount =
      await repository.recoverStaleProcessingTranscriptions(5 * 60 * 1000);

    expect(recoveredCount).toBe(1);
    expect(sessionCollection.updateMany).toHaveBeenCalledWith(
      expect.objectContaining({
        "transcription.status": "processing",
        "transcription.requestedAt": expect.objectContaining({
          $lte: expect.any(String)
        })
      }),
      expect.objectContaining({
        $set: expect.objectContaining({
          "transcription.status": "failed",
          "transcription.error":
            "Transcription timed out while processing. Please retry."
        })
      })
    );
  });

  it("cleans legacy null transcription fields in mongo", async () => {
    const updateManyCalls: Array<{
      filter: Record<string, null>;
      update: { $unset: Record<string, ""> };
    }> = [];
    const sessionCollection = {
      updateMany: vi.fn(async (filter, update) => {
        updateManyCalls.push({
          filter: filter as Record<string, null>,
          update: update as { $unset: Record<string, ""> }
        });

        return {
          modifiedCount:
            filter && "transcription.requestedAt" in (filter as Record<string, null>)
              ? 2
              : filter && "transcription.error" in (filter as Record<string, null>)
                ? 1
                : 0
        };
      }),
      findOne: vi.fn(),
      updateOne: vi.fn(),
      insertOne: vi.fn(),
      createIndex: vi.fn(),
      find: vi.fn()
    };
    const database = {
      collection: vi.fn(() => sessionCollection)
    };
    const client = {
      db: vi.fn(() => database),
      startSession: vi.fn()
    };

    const repository = new MongoSessionRepository(
      client as unknown as MongoClient,
      "medivoice_rewrite"
    );

    const cleanedCount = await repository.cleanupLegacyNullTranscriptionFields();

    expect(cleanedCount).toBe(3);
    expect(sessionCollection.updateMany).toHaveBeenCalledTimes(8);
    expect(updateManyCalls).toContainEqual({
      filter: { "transcription.requestedAt": null },
      update: { $unset: { "transcription.requestedAt": "" } }
    });
    expect(updateManyCalls).toContainEqual({
      filter: { "transcription.error": null },
      update: { $unset: { "transcription.error": "" } }
    });
    expect(updateManyCalls).not.toContainEqual({
      filter: { "transcription.requestedAt": null },
      update: expect.objectContaining({
        $unset: expect.objectContaining({
          "transcription.completedAt": ""
        })
      })
    });
  });

  it("parses legacy null transcription fields by dropping them", () => {
    const parsed = parseSessionDocument({
      _id: "316f0eda-fbb5-478e-b15f-c9f18f5bfc14",
      doctorId: "316f0eda-fbb5-478e-b15f-c9f18f5bfc13",
      patient: { name: "Anita Sharma", phoneNumber: "9876543210" },
      doctor: { name: "Dr. Rao" },
      status: "created",
      transcription: {
        status: "completed",
        provider: "gemini-2.5-flash",
        requestedAt: null,
        completedAt: "2026-03-18T00:02:00.000Z",
        failedAt: null,
        error: null,
        transcriptSegments: []
      },
      createdAt: "2026-03-18T00:00:00.000Z",
      updatedAt: "2026-03-18T00:02:00.000Z"
    } as unknown as Parameters<typeof parseSessionDocument>[0]);

    expect(parsed.transcription?.status).toBe("completed");
    expect(parsed.transcription?.requestedAt).toBeUndefined();
    expect(parsed.transcription?.failedAt).toBeUndefined();
    expect(parsed.transcription?.error).toBeUndefined();
  });

  it("parses legacy null report-context chunk fields by dropping them", () => {
    const parsed = parseSessionDocument({
      _id: "316f0eda-fbb5-478e-b15f-c9f18f5bfc14",
      doctorId: "316f0eda-fbb5-478e-b15f-c9f18f5bfc13",
      patient: { name: "Anita Sharma", phoneNumber: "9876543210" },
      doctor: { name: "Dr. Rao" },
      status: "created",
      reportContext: {
        status: "ready",
        chunks: [
          {
            id: "316f0eda-fbb5-478e-b15f-c9f18f5bfc12",
            sourceFileName: "lab.pdf",
            sourceIndex: 0,
            pageNumber: null,
            title: "HbA1c trend",
            category: "Lab",
            text: "HbA1c 8.2%",
            extractedAt: "2026-03-18T00:02:00.000Z"
          }
        ],
        selectedChunkIds: [],
        appliedChunkIds: []
      },
      createdAt: "2026-03-18T00:00:00.000Z",
      updatedAt: "2026-03-18T00:02:00.000Z"
    } as unknown as Parameters<typeof parseSessionDocument>[0]);

    expect(parsed.reportContext?.chunks).toHaveLength(1);
    expect(parsed.reportContext?.chunks[0]?.pageNumber).toBeUndefined();
  });
});
