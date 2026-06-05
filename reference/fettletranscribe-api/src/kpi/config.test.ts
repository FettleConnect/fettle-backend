import { describe, expect, it } from "vitest";

import {
  defaultKpiThresholds,
  resolveThresholdsForMetric,
  resolveThresholdsForSection
} from "./config.js";
import { computeKpiRating } from "./rating.js";

describe("kpi thresholds", () => {
  it("resolves section-specific thresholds", () => {
    const auth = resolveThresholdsForSection("auth");
    expect(auth.goodSuccessRate).toBeGreaterThan(defaultKpiThresholds.goodSuccessRate);
  });

  it("falls back to default thresholds for unknown sections", () => {
    const unknown = resolveThresholdsForSection("random_section");
    expect(unknown).toEqual(defaultKpiThresholds);
  });

  it("uses provided thresholds to compute rating", () => {
    const rating = computeKpiRating({
      successRate: 98.6,
      failureCount: 2,
      total: 100,
      thresholds: resolveThresholdsForSection("auth")
    });

    expect(rating).toBe("risky");
  });

  it("resolves metric-specific thresholds for session transcription stages", () => {
    const metricThresholds = resolveThresholdsForMetric(
      "session.transcription.provider_call",
      "session"
    );
    const sectionThresholds = resolveThresholdsForSection("session");

    expect(metricThresholds).not.toEqual(sectionThresholds);
    expect(metricThresholds.riskySuccessRate).toBeGreaterThanOrEqual(97);
  });
});
