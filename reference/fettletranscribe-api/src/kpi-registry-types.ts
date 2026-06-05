import type { KpiRating } from "./kpi/rating.js";

export interface KpiStat {
  metric: string;
  section: string;
  total: number;
  success: number;
  failure: number;
  lastSeenAt: string;
  totalDurationMs: number;
  avgDurationMs?: number;
  rating?: KpiRating;
  lastFailureAt?: string;
  lastErrorClass?: string;
  lastError?: string;
  lastProvider?: string;
}

export type KpiSampleStatus = "no_data" | "low_sample" | "ready";

export type KpiMetricScore = KpiStat & {
  successRate: number;
  rating: KpiRating;
  sampleStatus: KpiSampleStatus;
  minimumSampleSize: number;
};

export interface KpiScorecard {
  generatedAt: string;
  minimumSampleSize: number;
  notifications: KpiNotification[];
  sections: Array<{
    section: string;
    rating: KpiRating;
    sampleStatus: KpiSampleStatus;
    successRate: number;
    total: number;
    success: number;
    failure: number;
    metrics: KpiMetricScore[];
  }>;
}

export interface ScorecardFilters {
  section?: string;
  rating?: KpiRating;
  metricContains?: string;
  includeExpected?: boolean;
  window?: "today" | "yesterday" | "this_week" | "this_month" | "all_time";
}

export type ScorecardWindow = "today" | "yesterday" | "this_week" | "this_month" | "all_time";

export interface KpiNotification {
  id: string;
  timestamp: string;
  metric: string;
  section: string;
  title: string;
  reason: string;
  errorClass?: string;
  provider?: string;
  sessionId?: string;
  rating: KpiRating;
}

export const expectedPipelineMetrics = [
  "http.request",
  "auth.select",
  "auth.pin_login",
  "auth.pin_change",
  "provider.gemini.health",
  "provider.openai.health",
  "session.audio_upload",
  "session.transcription.queue",
  "session.transcription.provider_call",
  "session.transcription.end_to_end",
  "session.transcription.stale_recovery",
  "session.transcription.dead_letter",
  "session.report_context.upload",
  "session.report_context.extract",
  "report_context.preprocess",
  "report_context.provider_call",
  "report_context.extract_parse",
  "session.pdf_delivery_send",
  "delivery.twilio.whatsapp_status",
  "fettle_sync.queued",
  "fettle_sync.attempt",
  "fettle_sync.success",
  "fettle_sync.failure",
  "fettle_sync.dead_letter",
  "fettle_sync.stale_recovery"
] as const;
