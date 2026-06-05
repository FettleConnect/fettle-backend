import { describe, expect, it, vi } from "vitest";

import { AIProviderError } from "./errors.js";
import { emitKpi } from "./kpi.js";
import { ReliableTranscriptionAdapter } from "./reliable-transcription-adapter.js";
import type {
  TranscriptionAdapter,
  TranscriptionAdapterInput,
  TranscriptionAdapterResult
} from "./transcription-adapter.js";

vi.mock("./kpi.js", () => ({
  emitKpi: vi.fn()
}));

function buildInput(): TranscriptionAdapterInput {
  return {
    sessionId: "3b7fd684-b7fd-4f4f-8cd2-e8144d5fd8d8",
    audio: {
      fileName: "consultation.webm",
      mimeType: "audio/webm",
      durationSeconds: 30,
      data: Buffer.from("audio")
    },
    context: {
      patientName: "Patient",
      doctorName: "Doctor"
    }
  };
}

function buildResult(provider = "mock"): TranscriptionAdapterResult {
  return {
    provider,
    requiresRecovery: false,
    summary: "ok",
    transcriptSegments: [],
    metadata: {
      symptoms: [],
      diagnoses: [],
      careTeamNotes: []
    },
    draftDocument: {
      title: "Consultation Draft",
      clinicalSummary: "ok",
      medications: [],
      testsPrescribed: [],
      nextSteps: [],
      cautions: []
    }
  };
}

describe("ReliableTranscriptionAdapter", () => {
  it("emits success KPI when transcription succeeds", async () => {
    const delegate: TranscriptionAdapter = {
      providerName: "mock",
      transcribe: vi.fn(async () => buildResult())
    };

    const adapter = new ReliableTranscriptionAdapter(delegate, {
      maxRetries: 0,
      timeoutMs: 1_000
    });

    const result = await adapter.transcribe(buildInput());

    expect(result.summary).toBe("ok");
    expect(emitKpi).toHaveBeenCalledWith(
      "transcription.result",
      "success",
      expect.objectContaining({ provider: "mock" }),
      { scope: "transcription" }
    );
  });

  it("emits provider health KPI for Gemini success", async () => {
    const delegate: TranscriptionAdapter = {
      providerName: "gemini-2.5-flash",
      transcribe: vi.fn(async () => buildResult("gemini-2.5-flash"))
    };

    const adapter = new ReliableTranscriptionAdapter(delegate, {
      maxRetries: 0,
      timeoutMs: 1_000
    });

    await adapter.transcribe(buildInput());

    expect(emitKpi).toHaveBeenCalledWith(
      "provider.gemini.health",
      "success",
      expect.objectContaining({ provider: "gemini-2.5-flash" }),
      { scope: "provider" }
    );
  });

  it("maps circuit-open errors to provider-unavailable message", async () => {
    const delegate: TranscriptionAdapter = {
      providerName: "mock",
      transcribe: vi.fn(async () => {
        throw new Error("down");
      })
    };

    const adapter = new ReliableTranscriptionAdapter(delegate, {
      maxRetries: 0,
      timeoutMs: 1_000,
      maxFailuresBeforeOpen: 1,
      cooldownMs: 60_000
    });

    await expect(adapter.transcribe(buildInput())).rejects.toThrow("down");

    await expect(adapter.transcribe(buildInput())).rejects.toBeInstanceOf(AIProviderError);
  });

  it("does not retry non-retryable provider 4xx failures", async () => {
    const delegate: TranscriptionAdapter = {
      providerName: "openai",
      transcribe: vi.fn(async () => {
        throw new AIProviderError("OpenAI transcription failed with status 400.");
      })
    };
    const adapter = new ReliableTranscriptionAdapter(delegate, {
      maxRetries: 2,
      timeoutMs: 1_000
    });

    await expect(adapter.transcribe(buildInput())).rejects.toBeInstanceOf(
      AIProviderError
    );
    expect(delegate.transcribe).toHaveBeenCalledTimes(1);
  });
});
