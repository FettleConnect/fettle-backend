import {
  type ConsultationSession,
  type ReportContextChunk
} from "@medivoice/shared";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ZodError } from "zod";

import {
  AIProviderError,
  InvalidSessionStateError,
  MalformedAIOutputError,
  NotFoundError
} from "./errors.js";
import { getKpiSummary, resetKpiSummary } from "./kpi-registry.js";
import { InMemorySessionRepository } from "./session-repository.js";
import { SessionService } from "./session-service.js";
import { type ReportContextAdapter } from "./report-context-service.js";
import {
  MockGeminiTranscriptionAdapter,
  type TranscriptionAdapter,
  type TranscriptionAdapterInput,
  type TranscriptionAdapterResult
} from "./transcription-adapter.js";

const defaultDoctor = {
  doctorId: "316f0eda-fbb5-478e-b15f-c9f18f5bfc14",
  doctor: {
    name: "Dr. Rao",
    phoneNumber: "9123456789"
  }
};

function buildSessionDraft(overrides?: {
  patient?: {
    name: string;
    phoneNumber: string;
  };
  doctor?: {
    name: string;
    phoneNumber?: string;
  };
  doctorId?: string;
}) {
  return {
    patient: overrides?.patient ?? {
      name: "Anita Sharma",
      phoneNumber: "9876543210"
    },
    doctor: overrides?.doctor ?? defaultDoctor.doctor,
    doctorId: overrides?.doctorId ?? defaultDoctor.doctorId
  };
}

function createService(adapter?: TranscriptionAdapter) {
  return new SessionService(
    new InMemorySessionRepository(),
    adapter ?? new MockGeminiTranscriptionAdapter()
  );
}

describe("SessionService", () => {
  afterEach(() => {
    resetKpiSummary();
  });

  it("creates, lists, filters, and fetches sessions", async () => {
    const service = createService();

    const createdSession = await service.createSession(buildSessionDraft());

    const listedSessions = await service.listSessions({});
    const filteredSessions = await service.listSessions({ search: "9876" });
    const fetchedSession = await service.getSessionById(createdSession.id);

    expect(listedSessions).toHaveLength(1);
    expect(filteredSessions).toHaveLength(1);
    expect(fetchedSession.id).toBe(createdSession.id);
  });

  it("throws a validation error for invalid input", async () => {
    const service = createService();

    await expect(
      service.createSession({
        ...buildSessionDraft(),
        patient: {
          name: "",
          phoneNumber: "123"
        },
        doctor: {
          name: ""
        }
      })
    ).rejects.toBeInstanceOf(ZodError);
  });

  it("stores uploaded audio metadata against the session", async () => {
    const service = createService();
    const session = await service.createSession(
      buildSessionDraft({
        doctor: {
          name: "Dr. Rao"
        }
      })
    );

    const updatedSession = await service.uploadSessionAudio(session.id, {
      fileName: "consultation.webm",
      mimeType: "audio/webm",
      sizeBytes: 5,
      durationSeconds: 14.25,
      data: Buffer.from("audio")
    });

    expect(updatedSession.audioArtifact).toMatchObject({
      fileName: "consultation.webm",
      mimeType: "audio/webm",
      sizeBytes: 5,
      durationSeconds: 14.25
    });
    expect(updatedSession.updatedAt).toBe(updatedSession.audioArtifact?.uploadedAt);
  });

  it("replaces existing uploaded audio for the same session", async () => {
    const service = createService();
    const session = await service.createSession(
      buildSessionDraft({
        doctor: {
          name: "Dr. Rao"
        }
      })
    );

    const firstUpload = await service.uploadSessionAudio(session.id, {
      fileName: "consultation-1.webm",
      mimeType: "audio/webm",
      sizeBytes: 5,
      durationSeconds: 10,
      data: Buffer.from("audio-1")
    });
    const secondUpload = await service.uploadSessionAudio(session.id, {
      fileName: "consultation-2.webm",
      mimeType: "audio/webm",
      sizeBytes: 8,
      durationSeconds: 12.5,
      data: Buffer.from("audio-2")
    });

    expect(firstUpload.audioArtifact?.id).not.toBeUndefined();
    expect(secondUpload.audioArtifact).toMatchObject({
      id: session.id,
      fileName: "consultation-2.webm",
      sizeBytes: 8,
      durationSeconds: 12.5
    });
    expect(secondUpload.audioArtifact?.id).toBe(firstUpload.audioArtifact?.id);
  });

  it("rejects invalid audio uploads", async () => {
    const service = createService();
    const session = await service.createSession(
      buildSessionDraft({
        doctor: {
          name: "Dr. Rao"
        }
      })
    );

    await expect(
      service.uploadSessionAudio(session.id, {
        fileName: "",
        mimeType: "text/plain",
        sizeBytes: 0,
        durationSeconds: Number.NaN,
        data: Buffer.alloc(0)
      })
    ).rejects.toBeInstanceOf(ZodError);
  });

  it("rejects audio uploads that exceed the supported duration window", async () => {
    const service = createService();
    const session = await service.createSession(
      buildSessionDraft({
        doctor: {
          name: "Dr. Rao"
        }
      })
    );

    await expect(
      service.uploadSessionAudio(session.id, {
        fileName: "consultation.webm",
        mimeType: "audio/webm",
        sizeBytes: 5,
        durationSeconds: 4 * 60 * 60 + 1,
        data: Buffer.from("audio")
      })
    ).rejects.toBeInstanceOf(ZodError);
  });

  it("throws not found for a missing session", async () => {
    const service = createService();

    await expect(
      service.getSessionById("316f0eda-fbb5-478e-b15f-c9f18f5bfc14")
    ).rejects.toBeInstanceOf(NotFoundError);
  });

  it("treats invalid session ids as not found", async () => {
    const service = createService();

    await expect(service.getSessionById("not-a-uuid")).rejects.toBeInstanceOf(
      NotFoundError
    );
  });

  it("throws not found when uploading audio to a missing session", async () => {
    const service = createService();

    await expect(
      service.uploadSessionAudio("316f0eda-fbb5-478e-b15f-c9f18f5bfc14", {
        fileName: "consultation.webm",
        mimeType: "audio/webm",
        sizeBytes: 5,
        durationSeconds: 3,
        data: Buffer.from("audio")
      })
    ).rejects.toBeInstanceOf(NotFoundError);
  });

  it("persists typed transcription output for a recorded session", async () => {
    const service = createService();
    const session = await service.createSession(
      buildSessionDraft({
        doctor: {
          name: "Dr. Rao"
        }
      })
    );

    await service.uploadSessionAudio(session.id, {
      fileName: "consultation.webm",
      mimeType: "audio/webm",
      sizeBytes: 5,
      durationSeconds: 120,
      data: Buffer.from("audio")
    });

    const updatedSession = await service.submitSessionTranscription(session.id);

    expect(updatedSession.transcription?.status).toBe("completed");
    expect(updatedSession.transcription?.summary).toContain("Anita Sharma");
    expect(updatedSession.transcription?.transcriptSegments.length).toBeGreaterThan(
      0
    );
    expect(updatedSession.transcription?.draftDocument?.medications.length).toBe(
      2
    );
  });

  it("emits stage KPIs for successful transcription queue and processing", async () => {
    const service = createService();
    const session = await service.createSession(buildSessionDraft());
    await service.uploadSessionAudio(session.id, {
      fileName: "consultation.webm",
      mimeType: "audio/webm",
      sizeBytes: 5,
      durationSeconds: 120,
      data: Buffer.from("audio")
    });

    await service.submitSessionTranscription(session.id);
    const metrics = getKpiSummary().metrics;

    expect(
      metrics.find((metric) => metric.metric === "session.transcription.queue")
    ).toMatchObject({
      success: 1,
      failure: 0,
      total: 1
    });
    expect(
      metrics.find(
        (metric) => metric.metric === "session.transcription.provider_call"
      )
    ).toMatchObject({
      success: 1,
      failure: 0,
      total: 1
    });
    expect(
      metrics.find(
        (metric) => metric.metric === "session.transcription.end_to_end"
      )
    ).toMatchObject({
      success: 1,
      failure: 0,
      total: 1
    });
  });

  it("extracts selected report context and applies it to the draft", async () => {
    const service = createService();
    const session = await service.createSession(buildSessionDraft());

    await service.uploadSessionAudio(session.id, {
      fileName: "consultation.webm",
      mimeType: "audio/webm",
      sizeBytes: 5,
      durationSeconds: 120,
      data: Buffer.from("audio")
    });
    const transcribedSession = await service.submitSessionTranscription(session.id);
    const upload = await service.uploadReportContextFile(session.id, {
      fileName: "lab-report.pdf",
      mimeType: "application/pdf",
      sizeBytes: 12,
      data: Buffer.from("%PDF report")
    });
    const extractedSession = await service.extractReportContext(session.id, [
      upload.id
    ]);
    const chunk = extractedSession.reportContext?.chunks[0];

    expect(chunk).toMatchObject({
      sourceFileName: "lab-report.pdf",
      category: "Prior report"
    });

    const suggestion = await service.suggestReportContextDraft(session.id, [
      chunk!.id
    ]);

    expect(suggestion.changedFields).toEqual(["illnessHistory"]);
    expect(suggestion.draftDocument.illnessHistory).toContain("Report context");

    const appliedSession = await service.applyReportContextDraft(
      session.id,
      [chunk!.id],
      undefined,
      suggestion.draftDocument
    );

    expect(
      appliedSession.transcription?.draftDocument?.illnessHistory
    ).toContain("Report context");
    expect(appliedSession.reportContext?.appliedChunkIds).toContain(chunk!.id);
    expect(transcribedSession.transcription?.draftDocument).toBeDefined();
  });

  it("keeps extracted report context when upload cleanup fails", async () => {
    const repository = new CleanupFailingReportRepository();
    const service = new SessionService(
      repository,
      new MockGeminiTranscriptionAdapter()
    );
    const session = await service.createSession(buildSessionDraft());

    await service.uploadSessionAudio(session.id, {
      fileName: "consultation.webm",
      mimeType: "audio/webm",
      sizeBytes: 5,
      durationSeconds: 120,
      data: Buffer.from("audio")
    });
    await service.submitSessionTranscription(session.id);
    const upload = await service.uploadReportContextFile(session.id, {
      fileName: "lab-report.pdf",
      mimeType: "application/pdf",
      sizeBytes: 12,
      data: Buffer.from("%PDF report")
    });

    const extractedSession = await service.extractReportContext(session.id, [
      upload.id
    ]);

    expect(extractedSession.reportContext?.status).toBe("ready");
    expect(extractedSession.reportContext?.chunks).toHaveLength(1);
    expect(extractedSession.reportContext?.chunks[0]?.sourceFileName).toBe(
      "lab-report.pdf"
    );
  });

  it("preserves explicit report context chunk source file attribution", async () => {
    const service = new SessionService(
      new InMemorySessionRepository(),
      new MockGeminiTranscriptionAdapter(),
      undefined,
      undefined,
      new MultiChunkReportContextAdapter()
    );
    const session = await service.createSession(buildSessionDraft());

    await service.uploadSessionAudio(session.id, {
      fileName: "consultation.webm",
      mimeType: "audio/webm",
      sizeBytes: 5,
      durationSeconds: 120,
      data: Buffer.from("audio")
    });
    await service.submitSessionTranscription(session.id);
    const firstUpload = await service.uploadReportContextFile(session.id, {
      fileName: "lab-report.pdf",
      mimeType: "application/pdf",
      sizeBytes: 12,
      data: Buffer.from("%PDF report")
    });
    const secondUpload = await service.uploadReportContextFile(session.id, {
      fileName: "scan.png",
      mimeType: "image/png",
      sizeBytes: 12,
      data: Buffer.from("png report")
    });

    const extractedSession = await service.extractReportContext(session.id, [
      firstUpload.id,
      secondUpload.id
    ]);

    expect(
      extractedSession.reportContext?.chunks.map((chunk) => chunk.sourceFileName)
    ).toEqual(["scan.png", "scan.png"]);
  });

  it("requires completed transcription before report context upload", async () => {
    const service = createService();
    const session = await service.createSession(buildSessionDraft());

    await expect(
      service.uploadReportContextFile(session.id, {
        fileName: "report.pdf",
        mimeType: "application/pdf",
        sizeBytes: 12,
        data: Buffer.from("%PDF report")
      })
    ).rejects.toBeInstanceOf(InvalidSessionStateError);
  });

  it("lists previously uploaded report context files for the session", async () => {
    const service = createService();
    const session = await service.createSession(buildSessionDraft());

    await service.uploadSessionAudio(session.id, {
      fileName: "consultation.webm",
      mimeType: "audio/webm",
      sizeBytes: 5,
      durationSeconds: 120,
      data: Buffer.from("audio")
    });
    await service.submitSessionTranscription(session.id);

    await service.uploadReportContextFile(session.id, {
      fileName: "a-report.pdf",
      mimeType: "application/pdf",
      sizeBytes: 12,
      data: Buffer.from("%PDF report A")
    });
    await service.uploadReportContextFile(session.id, {
      fileName: "b-report.png",
      mimeType: "image/png",
      sizeBytes: 18,
      data: Buffer.from("PNG report B")
    });

    const uploads = await service.listReportContextUploads(session.id);
    expect(uploads.map((upload) => upload.fileName)).toEqual([
      "a-report.pdf",
      "b-report.png"
    ]);
  });

  it("recovers in-memory processing sessions after a restart", async () => {
    const repository = new InMemorySessionRepository();
    const service = new SessionService(
      repository,
      new MockGeminiTranscriptionAdapter()
    );
    const session = await service.createSession(buildSessionDraft());

    await service.uploadSessionAudio(session.id, {
      fileName: "consultation.webm",
      mimeType: "audio/webm",
      sizeBytes: 5,
      durationSeconds: 120,
      data: Buffer.from("audio")
    });
    await service.startSessionTranscription(session.id);

    const recoveredCount = await repository.recoverInterruptedTranscriptions();
    const recoveredSession = await repository.getSessionById(session.id);

    expect(recoveredCount).toBe(1);
    expect(recoveredSession?.transcription?.status).toBe("failed");
    expect(recoveredSession?.transcription?.error).toBe(
      "Transcription stopped when the server restarted. Please retry."
    );
  });

  it("requires uploaded audio before transcription", async () => {
    const service = createService();
    const session = await service.createSession(
      buildSessionDraft({
        doctor: {
          name: "Dr. Rao"
        }
      })
    );

    await expect(service.submitSessionTranscription(session.id)).rejects.toBeInstanceOf(
      InvalidSessionStateError
    );
  });

  it("rejects transcription when one is already in progress", async () => {
    const repository = new InMemorySessionRepository();
    const service = new SessionService(
      repository,
      new MockGeminiTranscriptionAdapter()
    );
    const session = await service.createSession(
      buildSessionDraft({
        doctor: {
          name: "Dr. Rao"
        }
      })
    );

    await service.uploadSessionAudio(session.id, {
      fileName: "consultation.webm",
      mimeType: "audio/webm",
      sizeBytes: 5,
      durationSeconds: 120,
      data: Buffer.from("audio")
    });

    await repository.markTranscriptionProcessing(
      session.id,
      "mock-gemini",
      "2026-03-30T00:00:00.000Z"
    );

    await expect(service.submitSessionTranscription(session.id)).rejects.toBeInstanceOf(
      InvalidSessionStateError
    );
  });

  it("rejects when another request wins the processing lock first", async () => {
    const repository = new RaceyInMemorySessionRepository();
    const service = new SessionService(
      repository,
      new MockGeminiTranscriptionAdapter()
    );
    const session = await service.createSession(
      buildSessionDraft({
        doctor: {
          name: "Dr. Rao"
        }
      })
    );

    await service.uploadSessionAudio(session.id, {
      fileName: "consultation.webm",
      mimeType: "audio/webm",
      sizeBytes: 5,
      durationSeconds: 120,
      data: Buffer.from("audio")
    });

    await expect(service.startSessionTranscription(session.id)).rejects.toBeInstanceOf(
      InvalidSessionStateError
    );
  });

  it("accepts same idempotency key when another request wins processing lock first", async () => {
    const repository = new RaceyInMemorySessionRepository();
    const service = new SessionService(
      repository,
      new MockGeminiTranscriptionAdapter()
    );
    const session = await service.createSession(
      buildSessionDraft({
        doctor: {
          name: "Dr. Rao"
        }
      })
    );

    await service.uploadSessionAudio(session.id, {
      fileName: "consultation.webm",
      mimeType: "audio/webm",
      sizeBytes: 5,
      durationSeconds: 120,
      data: Buffer.from("audio")
    });

    const started = await service.startSessionTranscription(
      session.id,
      undefined,
      "idem-12345678"
    );

    expect(started.transcription?.status).toBe("processing");
    expect(started.transcription?.requestKey).toBe("idem-12345678");
  });

  it("rejects audio that is too large for the current transcription provider", async () => {
    const service = createService(new SizeLimitedAdapter());
    const session = await service.createSession(
      buildSessionDraft({
        doctor: {
          name: "Dr. Rao"
        }
      })
    );

    await service.uploadSessionAudio(session.id, {
      fileName: "consultation.webm",
      mimeType: "audio/webm",
      sizeBytes: 5,
      durationSeconds: 120,
      data: Buffer.from("audio")
    });

    await expect(service.submitSessionTranscription(session.id)).rejects.toThrow(
      "too large for the current transcription provider"
    );
  });

  it("marks the session as failed when the adapter returns malformed output", async () => {
    const service = createService(new BrokenAdapter());
    const session = await service.createSession(
      buildSessionDraft({
        doctor: {
          name: "Dr. Rao"
        }
      })
    );

    await service.uploadSessionAudio(session.id, {
      fileName: "consultation.webm",
      mimeType: "audio/webm",
      sizeBytes: 5,
      durationSeconds: 120,
      data: Buffer.from("audio")
    });

    await expect(service.submitSessionTranscription(session.id)).rejects.toBeInstanceOf(
      MalformedAIOutputError
    );

    const failedSession = await service.getSessionById(session.id);

    expect(failedSession.transcription?.status).toBe("failed");
    expect(failedSession.transcription?.error).toContain("malformed");
  });

  it("emits failure stage KPIs when transcription provider returns malformed output", async () => {
    const service = createService(new BrokenAdapter());
    const session = await service.createSession(buildSessionDraft());
    await service.uploadSessionAudio(session.id, {
      fileName: "consultation.webm",
      mimeType: "audio/webm",
      sizeBytes: 5,
      durationSeconds: 120,
      data: Buffer.from("audio")
    });

    await expect(service.submitSessionTranscription(session.id)).rejects.toBeInstanceOf(
      MalformedAIOutputError
    );
    const metrics = getKpiSummary().metrics;

    expect(
      metrics.find((metric) => metric.metric === "session.transcription.queue")
    ).toMatchObject({
      success: 1,
      failure: 0,
      total: 1
    });
    expect(
      metrics.find(
        (metric) => metric.metric === "session.transcription.provider_call"
      )
    ).toMatchObject({
      success: 0,
      failure: 1,
      total: 1
    });
    expect(
      metrics.find(
        (metric) => metric.metric === "session.transcription.end_to_end"
      )
    ).toMatchObject({
      success: 0,
      failure: 1,
      total: 1
    });
  });

  it("rethrows the original adapter error when fail-state persistence also fails", async () => {
    const repository = new FailingInMemorySessionRepository();
    const service = new SessionService(repository, new BrokenAdapter());
    const session = await service.createSession(
      buildSessionDraft({
        doctor: {
          name: "Dr. Rao"
        }
      })
    );

    await service.uploadSessionAudio(session.id, {
      fileName: "consultation.webm",
      mimeType: "audio/webm",
      sizeBytes: 5,
      durationSeconds: 120,
      data: Buffer.from("audio")
    });

    await expect(service.submitSessionTranscription(session.id)).rejects.toBeInstanceOf(
      MalformedAIOutputError
    );
  });

  it("clears stale failure details after a successful retry", async () => {
    const adapter = new FailThenSucceedAdapter();
    const service = createService(adapter);
    const session = await service.createSession(
      buildSessionDraft({
        doctor: {
          name: "Dr. Rao"
        }
      })
    );

    await service.uploadSessionAudio(session.id, {
      fileName: "consultation.webm",
      mimeType: "audio/webm",
      sizeBytes: 5,
      durationSeconds: 120,
      data: Buffer.from("audio")
    });

    await expect(service.submitSessionTranscription(session.id)).rejects.toBeInstanceOf(
      MalformedAIOutputError
    );

    const completedSession = await service.submitSessionTranscription(session.id);

    expect(completedSession.transcription?.status).toBe("completed");
    expect(completedSession.transcription?.error).toBeUndefined();
    expect(completedSession.transcription?.failedAt).toBeUndefined();
    expect(completedSession.transcription?.completedAt).toBeDefined();
  });

  it("persists a clearer cancellation reason when the provider request is aborted", async () => {
    const infoSpy = vi.spyOn(console, "info").mockImplementation(() => {});
    const service = createService(new AbortAdapter());
    const session = await service.createSession(
      buildSessionDraft({
        doctor: {
          name: "Dr. Rao"
        }
      })
    );

    await service.uploadSessionAudio(session.id, {
      fileName: "consultation.webm",
      mimeType: "audio/webm",
      sizeBytes: 5,
      durationSeconds: 120,
      data: Buffer.from("audio")
    });

    await expect(service.submitSessionTranscription(session.id)).rejects.toMatchObject({
      name: "AbortError"
    });

    const failedSession = await service.getSessionById(session.id);

    expect(failedSession.transcription?.status).toBe("failed");
    expect(failedSession.transcription?.error).toBe(
      "Transcription was canceled before completion."
    );
    expect(infoSpy).toHaveBeenCalled();

    infoSpy.mockRestore();
  });

  it("saves edited draft data and generates a downloadable pdf artifact", async () => {
    const service = createService();
    const session = await service.createSession(
      buildSessionDraft({
        doctor: {
          name: "Dr. Rao"
        }
      })
    );

    await service.uploadSessionAudio(session.id, {
      fileName: "consultation.webm",
      mimeType: "audio/webm",
      sizeBytes: 5,
      durationSeconds: 120,
      data: Buffer.from("audio")
    });
    await service.submitSessionTranscription(session.id);

    const editedSession = await service.updateDraftDocument(session.id, {
      title: "Edited Consultation Draft",
      chiefComplaint: "Fever and cough",
      illnessHistory: "Symptoms worse at night.",
      clinicalSummary: "Symptoms are improving with supportive care.",
      medications: [
        {
          medicationName: "Paracetamol",
          dosage: "500 mg",
          instructions: "Twice daily"
        }
      ],
      testsPrescribed: ["CBC"],
      revisitDate: "2026-05-06",
      nextSteps: ["Rest"],
      cautions: ["Return if breathlessness develops"]
    });
    const pdfSession = await service.generateSessionPdf(session.id);
    const transcriptDownload = await service.getTranscriptDownload(session.id);
    const audioDownload = await service.getAudioDownload(session.id);
    const pdfDownload = await service.getPdfDownload(session.id);

    expect(editedSession.transcription?.draftDocument?.title).toBe(
      "Edited Consultation Draft"
    );
    expect(editedSession.transcription?.draftDocument?.chiefComplaint).toBe(
      "Fever and cough"
    );
    expect(editedSession.transcription?.draftDocument?.testsPrescribed).toEqual([
      "CBC"
    ]);
    expect(pdfSession.documentArtifact?.mimeType).toBe("application/pdf");
    expect(transcriptDownload.fileName).toContain("transcript.pdf");
    expect(transcriptDownload.mimeType).toBe("application/pdf");
    expect(transcriptDownload.data.subarray(0, 8).toString()).toContain("%PDF-1.4");
    expect(audioDownload.fileName).toBe("consultation.webm");
    expect(pdfDownload.fileName).toContain(".pdf");
    expect(pdfDownload.data.subarray(0, 8).toString()).toContain("%PDF-1.4");
  });

  it("marks transcription as exhausted after retry budget and blocks further retries", async () => {
    const service = createService(new AlwaysFailAdapter());
    const session = await service.createSession(buildSessionDraft());
    await service.uploadSessionAudio(session.id, {
      fileName: "consultation.webm",
      mimeType: "audio/webm",
      sizeBytes: 5,
      durationSeconds: 120,
      data: Buffer.from("audio")
    });

    for (let i = 0; i < 3; i += 1) {
      await expect(service.submitSessionTranscription(session.id)).rejects.toBeInstanceOf(
        AIProviderError
      );
    }

    const failedSession = await service.getSessionById(session.id);
    expect(failedSession.transcription?.exhaustedRetryBudget).toBe(true);
    expect(failedSession.transcription?.attemptCount).toBe(3);

    await expect(service.startSessionTranscription(session.id)).rejects.toThrow(
      "retry budget is exhausted"
    );
  });

  it("treats duplicate transcription start with same idempotency key as safe replay", async () => {
    const service = createService(new HangingAdapter());
    const session = await service.createSession(buildSessionDraft());
    await service.uploadSessionAudio(session.id, {
      fileName: "consultation.webm",
      mimeType: "audio/webm",
      sizeBytes: 5,
      durationSeconds: 120,
      data: Buffer.from("audio")
    });

    const first = await service.startSessionTranscription(
      session.id,
      undefined,
      "req-12345678"
    );
    const second = await service.startSessionTranscription(
      session.id,
      undefined,
      "req-12345678"
    );

    expect(first.transcription?.status).toBe("processing");
    expect(second.transcription?.status).toBe("processing");
    expect(second.transcription?.requestKey).toBe("req-12345678");

    await expect(
      service.startSessionTranscription(session.id, undefined, "another-key")
    ).rejects.toBeInstanceOf(InvalidSessionStateError);
  });
});

class BrokenAdapter implements TranscriptionAdapter {
  readonly providerName = "broken-adapter";

  async transcribe(
    _input: TranscriptionAdapterInput
  ): Promise<TranscriptionAdapterResult> {
    throw new MalformedAIOutputError(
      "Gemini returned malformed JSON for the transcription payload."
    );
  }
}

class AbortAdapter implements TranscriptionAdapter {
  readonly providerName = "abort-adapter";

  async transcribe(
    _input: TranscriptionAdapterInput
  ): Promise<TranscriptionAdapterResult> {
    throw new DOMException("The operation was aborted.", "AbortError");
  }
}

class FailThenSucceedAdapter implements TranscriptionAdapter {
  readonly providerName = "fail-then-succeed";
  private attempts = 0;

  async transcribe(
    _input: TranscriptionAdapterInput
  ): Promise<TranscriptionAdapterResult> {
    this.attempts += 1;

    if (this.attempts === 1) {
      throw new MalformedAIOutputError(
        "Gemini returned malformed JSON for the transcription payload."
      );
    }

    return {
      provider: this.providerName,
      requiresRecovery: false,
      summary: "Recovered summary",
      transcriptSegments: [
        {
          id: "11111111-1111-4111-8111-111111111111",
          speaker: "doctor",
          speakerLabel: "Dr. Rao",
          text: "How are you feeling after taking the medicine?",
          startedAtSeconds: 0,
          endedAtSeconds: 4
        },
        {
          id: "22222222-2222-4222-8222-222222222222",
          speaker: "patient",
          speakerLabel: "Anita Sharma",
          text: "The cough is better and the fever has not returned.",
          startedAtSeconds: 48,
          endedAtSeconds: 55
        }
      ],
      metadata: {
        symptoms: [],
        diagnoses: [],
        careTeamNotes: []
      },
      draftDocument: {
        title: "Consultation Draft",
        clinicalSummary: "Recovered summary",
        medications: [],
        testsPrescribed: [],
        nextSteps: [],
        cautions: []
      }
    };
  }
}

class SizeLimitedAdapter implements TranscriptionAdapter {
  readonly providerName = "size-limited-adapter";
  readonly maxAudioBytes = 1;

  async transcribe(
    _input: TranscriptionAdapterInput
  ): Promise<TranscriptionAdapterResult> {
    throw new Error("This adapter should not be called for oversized audio.");
  }
}

class FailingInMemorySessionRepository extends InMemorySessionRepository {
  override async failTranscription(
    ...args: Parameters<InMemorySessionRepository["failTranscription"]>
  ): ReturnType<InMemorySessionRepository["failTranscription"]> {
    await super.failTranscription(...args);
    throw new Error("failTranscription write failed");
  }
}

class CleanupFailingReportRepository extends InMemorySessionRepository {
  override async deleteReportContextUpload(
    _sessionId: string,
    _uploadId: string
  ): Promise<void> {
    throw new Error("cleanup failed");
  }
}

class MultiChunkReportContextAdapter implements ReportContextAdapter {
  readonly providerName = "multi-chunk-report-context";

  async extractChunks(): Promise<ReportContextChunk[]> {
    const extractedAt = new Date().toISOString();

    return [
      {
        id: "7f36317d-1388-4979-bfb2-291e3e3cbe86",
        sourceFileName: "scan.png",
        title: "Finding one",
        category: "Imaging",
        text: "First imaging finding.",
        extractedAt
      },
      {
        id: "b4fb21af-bde1-483f-8e5c-404f743d7997",
        sourceFileName: "scan.png",
        title: "Finding two",
        category: "Imaging",
        text: "Second imaging finding.",
        extractedAt
      }
    ];
  }

  async suggestDraft(
    _input: Parameters<ReportContextAdapter["suggestDraft"]>[0]
  ): ReturnType<ReportContextAdapter["suggestDraft"]> {
    throw new Error("suggestDraft should not be called.");
  }
}

class RaceyInMemorySessionRepository extends InMemorySessionRepository {
  override async markTranscriptionProcessing(
    id: string,
    provider: string,
    _requestedAt: string,
    maxAttempts?: number,
    requestKey?: string
  ): Promise<ConsultationSession | null> {
    return super.markTranscriptionProcessing(
      id,
      provider,
      "2026-03-30T00:00:00.000Z",
      maxAttempts,
      requestKey
    );
  }
}

class AlwaysFailAdapter implements TranscriptionAdapter {
  readonly providerName = "always-fail";

  async transcribe(): Promise<TranscriptionAdapterResult> {
    throw new AIProviderError("Provider unavailable.");
  }
}

class HangingAdapter implements TranscriptionAdapter {
  readonly providerName = "hanging";

  async transcribe(): Promise<TranscriptionAdapterResult> {
    await new Promise(() => undefined);
    throw new Error("unreachable");
  }
}
