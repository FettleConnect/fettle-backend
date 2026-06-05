import { type PdfDeliveryJob } from "@medivoice/shared";
import { describe, expect, it, vi } from "vitest";

import { InMemoryPdfDeliveryRepository } from "./pdf-delivery-repository.js";
import {
  PdfDeliveryService,
  PdfDeliveryWorker
} from "./pdf-delivery-service.js";
import { InMemorySessionRepository } from "./session-repository.js";
import { SessionService } from "./session-service.js";
import { MockGeminiTranscriptionAdapter } from "./transcription-adapter.js";

const sessionDraft = {
  patient: {
    name: "Anita Sharma",
    phoneNumber: "9876543210"
  },
  doctorId: "316f0eda-fbb5-478e-b15f-c9f18f5bfc14",
  doctor: {
    name: "Dr. Rao",
    phoneNumber: "9123456789"
  }
};

describe("PdfDeliveryService", () => {
  it("defaults to patient delivery only when no custom settings are saved", async () => {
    const repository = new InMemoryPdfDeliveryRepository();
    const service = new PdfDeliveryService(
      repository,
      new InMemorySessionRepository()
    );
    const status = await service.getSessionDeliveryStatus(
      "316f0eda-fbb5-478e-b15f-c9f18f5bfc14"
    );

    expect(status.settings.patientEnabled).toBe(true);
    expect(status.settings.pharmacyEnabled).toBe(false);
    expect(status.jobs).toEqual([]);
  });

  it("queues patient and pharmacy jobs after generating a consultation pdf", async () => {
    const pdfDeliveryRepository = new InMemoryPdfDeliveryRepository();
    const sessionRepository = new InMemorySessionRepository();
    const pdfDeliveryService = new PdfDeliveryService(
      pdfDeliveryRepository,
      sessionRepository
    );
    const sessionService = new SessionService(
      sessionRepository,
      new MockGeminiTranscriptionAdapter(),
      undefined,
      pdfDeliveryService
    );
    const session = await sessionService.createSession(sessionDraft);

    await sessionService.prepareSessionPdfDelivery(session.id, {
      patientEnabled: true,
      pharmacyEnabled: true,
      pharmacy: {
        name: "CarePlus Pharmacy",
        phoneNumber: "9988776655"
      }
    });
    await sessionService.uploadSessionAudio(session.id, {
      fileName: "consultation.webm",
      mimeType: "audio/webm",
      sizeBytes: 5,
      durationSeconds: 120,
      data: Buffer.from("audio")
    });
    await sessionService.submitSessionTranscription(session.id);

    await sessionService.generateSessionPdf(session.id);

    const status = await sessionService.getSessionPdfDeliveryStatus(session.id);

    expect(status.jobs).toHaveLength(2);
    expect(
      status.jobs.map((job: PdfDeliveryJob) => job.recipientType).sort()
    ).toEqual([
      "patient",
      "pharmacy"
    ]);
    expect(
      status.jobs.every((job: PdfDeliveryJob) => job.status === "pending")
    ).toBe(true);
  });

  it("processes queued jobs through the worker and marks them as queued", async () => {
    const pdfDeliveryRepository = new InMemoryPdfDeliveryRepository();
    const sessionRepository = new InMemorySessionRepository();
    const pdfDeliveryService = new PdfDeliveryService(
      pdfDeliveryRepository,
      sessionRepository
    );
    const worker = new PdfDeliveryWorker(pdfDeliveryService);
    const sessionService = new SessionService(
      sessionRepository,
      new MockGeminiTranscriptionAdapter(),
      undefined,
      pdfDeliveryService
    );
    const session = await sessionService.createSession(sessionDraft);

    await sessionService.uploadSessionAudio(session.id, {
      fileName: "consultation.webm",
      mimeType: "audio/webm",
      sizeBytes: 5,
      durationSeconds: 120,
      data: Buffer.from("audio")
    });
    await sessionService.submitSessionTranscription(session.id);
    await sessionService.generateSessionPdf(session.id);

    const processed = await worker.processNextBatch();
    const status = await sessionService.getSessionPdfDeliveryStatus(session.id);

    expect(processed).toHaveLength(1);
    expect(processed[0]?.status).toBe("queued");
    expect(processed[0]?.provider).toBe("mock-whatsapp");
    expect(status.jobs[0]?.status).toBe("queued");
  });

  it("retries transient sender failures and still marks job queued", async () => {
    let attempts = 0;
    const sender = {
      async send(context: { job: PdfDeliveryJob }) {
        attempts += 1;
        if (attempts === 1) {
          throw new Error("temporary provider failure");
        }
        return {
          provider: "mock-whatsapp",
          providerMessageId: `mock-${context.job.id}`
        };
      }
    };
    const pdfDeliveryRepository = new InMemoryPdfDeliveryRepository();
    const sessionRepository = new InMemorySessionRepository();
    const pdfDeliveryService = new PdfDeliveryService(
      pdfDeliveryRepository,
      sessionRepository,
      undefined,
      sender
    );
    const worker = new PdfDeliveryWorker(pdfDeliveryService);
    const sessionService = new SessionService(
      sessionRepository,
      new MockGeminiTranscriptionAdapter(),
      undefined,
      pdfDeliveryService
    );
    const session = await sessionService.createSession(sessionDraft);

    await sessionService.uploadSessionAudio(session.id, {
      fileName: "consultation.webm",
      mimeType: "audio/webm",
      sizeBytes: 5,
      durationSeconds: 120,
      data: Buffer.from("audio")
    });
    await sessionService.submitSessionTranscription(session.id);
    await sessionService.generateSessionPdf(session.id);

    const processed = await worker.processNextBatch();
    const status = await sessionService.getSessionPdfDeliveryStatus(session.id);

    expect(attempts).toBe(2);
    expect(processed[0]?.status).toBe("queued");
    expect(status.jobs[0]?.status).toBe("queued");
  });

  it("serves signed public media assets for Twilio fetches", async () => {
    let capturedMediaUrl = "";
    const sender = {
      async send(context: { job: PdfDeliveryJob; mediaUrl: string }) {
        capturedMediaUrl = context.mediaUrl;
        return {
          provider: "mock-whatsapp",
          providerMessageId: `mock-${context.job.id}`
        };
      }
    };
    const pdfDeliveryRepository = new InMemoryPdfDeliveryRepository();
    const sessionRepository = new InMemorySessionRepository();
    const pdfDeliveryService = new PdfDeliveryService(
      pdfDeliveryRepository,
      sessionRepository,
      undefined,
      sender,
      {
        baseUrl: "https://transcript.fettleconnect.com",
        signingSecret: "test-secret",
        tokenTtlSeconds: 900
      }
    );
    const worker = new PdfDeliveryWorker(pdfDeliveryService);
    const sessionService = new SessionService(
      sessionRepository,
      new MockGeminiTranscriptionAdapter(),
      undefined,
      pdfDeliveryService
    );
    const session = await sessionService.createSession(sessionDraft);

    await sessionService.uploadSessionAudio(session.id, {
      fileName: "consultation.webm",
      mimeType: "audio/webm",
      sizeBytes: 5,
      durationSeconds: 120,
      data: Buffer.from("audio")
    });
    await sessionService.submitSessionTranscription(session.id);
    await sessionService.generateSessionPdf(session.id);
    await worker.processNextBatch();

    const mediaUrl = new URL(capturedMediaUrl);
    expect(mediaUrl.origin).toBe("https://transcript.fettleconnect.com");
    expect(mediaUrl.pathname).toMatch(/^\/public\/pdf-delivery\/media\/.+\.pdf$/);

    const token = mediaUrl.searchParams.get("token");
    expect(token).toBeTruthy();

    const asset = await pdfDeliveryService.getPublicMediaAsset(token!);
    expect(asset?.sessionId).toBe(session.id);
    expect(asset?.mimeType).toBe("application/pdf");
    expect(asset?.data.length).toBeGreaterThan(0);
  });

  it("does not block retries for stale queued jobs", async () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-04-08T00:00:00.000Z"));
    try {
      const pdfDeliveryRepository = new InMemoryPdfDeliveryRepository();
      const sessionRepository = new InMemorySessionRepository();
      const pdfDeliveryService = new PdfDeliveryService(
        pdfDeliveryRepository,
        sessionRepository
      );
      const sessionService = new SessionService(
        sessionRepository,
        new MockGeminiTranscriptionAdapter(),
        undefined,
        pdfDeliveryService
      );
      const session = await sessionService.createSession(sessionDraft);

      await sessionService.uploadSessionAudio(session.id, {
        fileName: "consultation.webm",
        mimeType: "audio/webm",
        sizeBytes: 5,
        durationSeconds: 120,
        data: Buffer.from("audio")
      });
      await sessionService.submitSessionTranscription(session.id);
      await sessionService.generateSessionPdf(session.id);

      const firstSendStatus = await sessionService.sendSessionPdfDelivery(session.id, {
        patientEnabled: true,
        pharmacyEnabled: false
      });
      expect(firstSendStatus.jobs[0]?.status).toBe("pending");

      const firstJob = firstSendStatus.jobs[0];
      if (!firstJob) {
        throw new Error("Expected first delivery job to exist.");
      }

      await pdfDeliveryRepository.updateJobStatus(firstJob.id, "queued", {
        provider: "twilio-whatsapp",
        providerMessageId: "MM-queued",
        sentAt: new Date().toISOString()
      });

      vi.setSystemTime(new Date("2026-04-08T00:03:30.000Z"));

      const retryStatus = await sessionService.sendSessionPdfDelivery(session.id, {
        patientEnabled: true,
        pharmacyEnabled: false
      });
      expect(retryStatus.jobs).toHaveLength(2);
      expect(retryStatus.jobs[0]?.status).toBe("pending");
    } finally {
      vi.useRealTimers();
    }
  });

  it("allows resending after a previous patient job is already sent", async () => {
    const pdfDeliveryRepository = new InMemoryPdfDeliveryRepository();
    const sessionRepository = new InMemorySessionRepository();
    const pdfDeliveryService = new PdfDeliveryService(
      pdfDeliveryRepository,
      sessionRepository
    );
    const sessionService = new SessionService(
      sessionRepository,
      new MockGeminiTranscriptionAdapter(),
      undefined,
      pdfDeliveryService
    );
    const session = await sessionService.createSession(sessionDraft);

    await sessionService.uploadSessionAudio(session.id, {
      fileName: "consultation.webm",
      mimeType: "audio/webm",
      sizeBytes: 5,
      durationSeconds: 120,
      data: Buffer.from("audio")
    });
    await sessionService.submitSessionTranscription(session.id);
    await sessionService.generateSessionPdf(session.id);

    const firstSendStatus = await sessionService.sendSessionPdfDelivery(session.id, {
      patientEnabled: true,
      pharmacyEnabled: false
    });
    const firstJob = firstSendStatus.jobs[0];

    if (!firstJob) {
      throw new Error("Expected first delivery job to exist.");
    }

    await pdfDeliveryRepository.updateJobStatus(firstJob.id, "sent", {
      provider: "twilio-whatsapp",
      providerMessageId: "MM-first-sent",
      sentAt: new Date().toISOString()
    });

    const retryStatus = await sessionService.sendSessionPdfDelivery(session.id, {
      patientEnabled: true,
      pharmacyEnabled: false
    });

    expect(retryStatus.jobs).toHaveLength(2);
    expect(retryStatus.jobs.some((job) => job.status === "pending")).toBe(true);
  });

  it("ignores out-of-order provider statuses that would regress a failed job", async () => {
    const pdfDeliveryRepository = new InMemoryPdfDeliveryRepository();
    const sessionRepository = new InMemorySessionRepository();
    const pdfDeliveryService = new PdfDeliveryService(
      pdfDeliveryRepository,
      sessionRepository
    );
    const sessionService = new SessionService(
      sessionRepository,
      new MockGeminiTranscriptionAdapter(),
      undefined,
      pdfDeliveryService
    );
    const session = await sessionService.createSession(sessionDraft);

    await sessionService.uploadSessionAudio(session.id, {
      fileName: "consultation.webm",
      mimeType: "audio/webm",
      sizeBytes: 5,
      durationSeconds: 120,
      data: Buffer.from("audio")
    });
    await sessionService.submitSessionTranscription(session.id);
    await sessionService.generateSessionPdf(session.id);

    const firstSendStatus = await sessionService.sendSessionPdfDelivery(session.id, {
      patientEnabled: true,
      pharmacyEnabled: false
    });
    const firstJob = firstSendStatus.jobs[0];

    if (!firstJob) {
      throw new Error("Expected first delivery job to exist.");
    }

    await pdfDeliveryRepository.updateJobStatus(firstJob.id, "queued", {
      provider: "twilio-whatsapp",
      providerMessageId: "MM-webhook-order",
      sentAt: new Date().toISOString()
    });

    const failed = await pdfDeliveryService.handleProviderStatusUpdate({
      providerMessageId: "MM-webhook-order",
      providerStatus: "undelivered"
    });
    expect(failed?.status).toBe("failed");

    const outOfOrder = await pdfDeliveryService.handleProviderStatusUpdate({
      providerMessageId: "MM-webhook-order",
      providerStatus: "sent"
    });
    expect(outOfOrder?.status).toBe("failed");

    const latestStatus = await sessionService.getSessionPdfDeliveryStatus(session.id);
    expect(latestStatus.jobs[0]?.status).toBe("failed");
  });
});
