export interface KpiRatingThresholds {
  excellentSuccessRate: number;
  excellentMaxFailures: number;
  goodSuccessRate: number;
  riskySuccessRate: number;
}

export const defaultKpiThresholds: KpiRatingThresholds = {
  excellentSuccessRate: 99.5,
  excellentMaxFailures: 1,
  goodSuccessRate: 98,
  riskySuccessRate: 95
};

export const kpiSectionThresholds: Record<string, KpiRatingThresholds> = {
  auth: {
    excellentSuccessRate: 99.7,
    excellentMaxFailures: 1,
    goodSuccessRate: 99,
    riskySuccessRate: 97
  },
  session: {
    excellentSuccessRate: 99.5,
    excellentMaxFailures: 2,
    goodSuccessRate: 98.5,
    riskySuccessRate: 96
  },
  delivery: {
    excellentSuccessRate: 99.2,
    excellentMaxFailures: 2,
    goodSuccessRate: 98,
    riskySuccessRate: 95
  },
  report_context: {
    excellentSuccessRate: 99,
    excellentMaxFailures: 2,
    goodSuccessRate: 97.5,
    riskySuccessRate: 94
  },
  fettle_sync: {
    excellentSuccessRate: 99,
    excellentMaxFailures: 1,
    goodSuccessRate: 97.5,
    riskySuccessRate: 95
  },
  http: {
    excellentSuccessRate: 99.9,
    excellentMaxFailures: 5,
    goodSuccessRate: 99.5,
    riskySuccessRate: 98
  },
  other: defaultKpiThresholds
};

const metricThresholdOverrides: Record<string, KpiRatingThresholds> = {
  "session.transcription.queue": {
    excellentSuccessRate: 99.7,
    excellentMaxFailures: 1,
    goodSuccessRate: 99.2,
    riskySuccessRate: 98
  },
  "session.transcription.provider_call": {
    excellentSuccessRate: 99.2,
    excellentMaxFailures: 2,
    goodSuccessRate: 98.5,
    riskySuccessRate: 97
  },
  "session.transcription.end_to_end": {
    excellentSuccessRate: 99.3,
    excellentMaxFailures: 2,
    goodSuccessRate: 98.7,
    riskySuccessRate: 97
  }
};

export function resolveThresholdsForSection(section: string): KpiRatingThresholds {
  return kpiSectionThresholds[section] ?? defaultKpiThresholds;
}

export function resolveThresholdsForMetric(
  metric: string,
  section: string
): KpiRatingThresholds {
  return metricThresholdOverrides[metric] ?? resolveThresholdsForSection(section);
}
