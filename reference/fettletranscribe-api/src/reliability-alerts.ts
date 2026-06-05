import { type KpiRating } from "./kpi/rating.js";
import { getFocusedKpiScorecard } from "./kpi-registry.js";

export interface ReliabilityAlert {
  severity: "critical" | "warning";
  section: string;
  metric?: string;
  rating: KpiRating;
  message: string;
}

export function getReliabilityAlerts(): {
  generatedAt: string;
  alerts: ReliabilityAlert[];
} {
  const scorecard = getFocusedKpiScorecard();
  const alerts: ReliabilityAlert[] = [];

  for (const section of scorecard.sections) {
    if (section.rating === "critical" || section.rating === "risky") {
      alerts.push({
        severity: section.rating === "critical" ? "critical" : "warning",
        section: section.section,
        rating: section.rating,
        message: `Section ${section.section} is ${section.rating}.`
      });
    }

    for (const metric of section.metrics) {
      if (metric.rating === "critical" || metric.rating === "risky") {
        alerts.push({
          severity: metric.rating === "critical" ? "critical" : "warning",
          section: section.section,
          metric: metric.metric,
          rating: metric.rating,
          message: `Metric ${metric.metric} is ${metric.rating} (${metric.successRate}% success).`
        });
      }
    }
  }

  return {
    generatedAt: new Date().toISOString(),
    alerts
  };
}
