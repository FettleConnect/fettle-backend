import { type KpiRatingThresholds } from "./config.js";

export type KpiRating = "excellent" | "good" | "risky" | "critical";

export function computeKpiRating(input: {
  successRate: number;
  failureCount: number;
  total: number;
  thresholds: KpiRatingThresholds;
}): KpiRating {
  if (input.total === 0) {
    return "good";
  }

  if (
    input.successRate >= input.thresholds.excellentSuccessRate &&
    input.failureCount <= input.thresholds.excellentMaxFailures
  ) {
    return "excellent";
  }

  if (input.successRate >= input.thresholds.goodSuccessRate) {
    return "good";
  }

  if (input.successRate >= input.thresholds.riskySuccessRate) {
    return "risky";
  }

  return "critical";
}

export function aggregateSectionRating(ratings: KpiRating[]): KpiRating {
  if (!ratings.length) {
    return "good";
  }

  if (ratings.includes("critical")) {
    return "critical";
  }

  if (ratings.includes("risky")) {
    return "risky";
  }

  if (ratings.includes("good")) {
    return "good";
  }

  return "excellent";
}
