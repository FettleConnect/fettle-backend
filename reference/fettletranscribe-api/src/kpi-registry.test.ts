import { afterEach, describe, expect, it, vi } from "vitest";

import {
  configureKpiEventStore,
  configureKpiMinimumSampleSize,
  getFilteredKpiScorecard,
  getKpiSummary,
  recordKpi,
  resetKpiSummary,
  warmKpiSummaryFromStore
} from "./kpi-registry.js";
import type { KpiEventStore, PersistedKpiEvent } from "./kpi-event-store.js";

describe("kpi-registry persistence", () => {
  afterEach(() => {
    configureKpiEventStore({ store: null });
    configureKpiMinimumSampleSize(1);
    resetKpiSummary();
    vi.restoreAllMocks();
  });

  it("persists recorded KPI events to configured store", async () => {
    const append = vi.fn(async () => undefined);
    const listSince = vi.fn(async () => [] as PersistedKpiEvent[]);
    const store: KpiEventStore = { append, listSince };
    configureKpiEventStore({ store });

    recordKpi("session.transcription.provider_call", "failure", {
      sessionId: "session-1",
      error: "OpenAI transcription failed with status 503.",
      provider: "openai"
    });

    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(append).toHaveBeenCalledTimes(1);
    expect(append).toHaveBeenCalledWith(
      expect.objectContaining({
        metric: "session.transcription.provider_call",
        status: "failure",
        section: "session",
        provider: "openai",
        sessionId: "session-1"
      })
    );
  });

  it("warms in-memory KPI summary from persisted events", async () => {
    const listSince = vi.fn(async () => [
      {
        timestamp: "2026-05-25T10:00:00.000Z",
        metric: "session.transcription.provider_call",
        section: "session",
        status: "success"
      },
      {
        timestamp: "2026-05-25T10:05:00.000Z",
        metric: "session.transcription.provider_call",
        section: "session",
        status: "failure",
        error: "provider unavailable"
      }
    ] as PersistedKpiEvent[]);

    const store: KpiEventStore = {
      append: vi.fn(async () => undefined),
      listSince
    };
    configureKpiEventStore({ store });

    const loaded = await warmKpiSummaryFromStore("2026-05-25T00:00:00.000Z");

    expect(loaded).toBe(2);
    const metric = getKpiSummary().metrics.find(
      (entry) => entry.metric === "session.transcription.provider_call"
    );
    expect(metric?.total).toBe(2);
    expect(metric?.success).toBe(1);
    expect(metric?.failure).toBe(1);
  });

  it("marks expected metrics as no-data and gates low samples", () => {
    configureKpiMinimumSampleSize(3);
    recordKpi("provider.gemini.health", "success", {
      provider: "gemini-2.5-flash"
    });

    const scorecard = getFilteredKpiScorecard({
      includeExpected: true,
      section: "provider"
    });
    const metrics = scorecard.sections.flatMap((section) => section.metrics);

    expect(scorecard.minimumSampleSize).toBe(3);
    expect(metrics.find((metric) => metric.metric === "provider.gemini.health"))
      .toMatchObject({
        total: 1,
        sampleStatus: "low_sample",
        minimumSampleSize: 3
      });
    expect(metrics.find((metric) => metric.metric === "provider.openai.health"))
      .toMatchObject({
        total: 0,
        sampleStatus: "no_data",
        minimumSampleSize: 3
      });
  });
});
