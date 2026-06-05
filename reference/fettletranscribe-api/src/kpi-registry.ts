import { type LogContext } from "./logger.js";
import {
  aggregateSectionRating,
  computeKpiRating,
  type KpiRating
} from "./kpi/rating.js";
import { resolveThresholdsForMetric } from "./kpi/config.js";
import {
  type KpiEventStore,
  type PersistedKpiEvent
} from "./kpi-event-store.js";
import {
  expectedPipelineMetrics,
  type KpiMetricScore,
  type KpiNotification,
  type KpiSampleStatus,
  type KpiScorecard,
  type KpiStat,
  type ScorecardFilters,
  type ScorecardWindow
} from "./kpi-registry-types.js";

const stats = new Map<string, KpiStat>();
const recentFailures: KpiNotification[] = [];
const recentEvents: PersistedKpiEvent[] = [];
const maxRecentFailures = 20;
const maxRecentEvents = 3000;
let eventStore: KpiEventStore | null = null;
let onEventStoreError: ((error: unknown) => void) | null = null;
let minimumSampleSize = 1;
export function recordKpi(
  metric: string,
  status: "success" | "failure",
  context: LogContext
): void {
  const timestamp = new Date().toISOString();
  const event: PersistedKpiEvent = {
    timestamp,
    metric,
    section: extractSection(metric),
    status,
    ...(typeof context.elapsedMs === "number" ? { elapsedMs: context.elapsedMs } : {}),
    ...(typeof context.errorClass === "string" ? { errorClass: context.errorClass } : {}),
    ...(typeof context.error === "string" ? { error: context.error } : {}),
    ...(typeof context.provider === "string" ? { provider: context.provider } : {}),
    ...(typeof context.sessionId === "string" ? { sessionId: context.sessionId } : {}),
    ...(typeof context.operation === "string" ? { operation: context.operation } : {}),
    ...(typeof context.fallbackUsed === "boolean" ? { fallbackUsed: context.fallbackUsed } : {}),
    ...(typeof context.fallbackProviderChain === "string"
      ? { fallbackProviderChain: context.fallbackProviderChain }
      : {})
  };
  applyKpiEvent(event);
  void persistKpiEvent(event);
}

export async function warmKpiSummaryFromStore(since: string): Promise<number> {
  if (!eventStore) {
    return 0;
  }

  const events = await eventStore.listSince(since);
  for (const event of events) {
    applyKpiEvent(event, true);
  }
  return events.length;
}

export function configureKpiEventStore(options: {
  store: KpiEventStore | null;
  onError?: (error: unknown) => void;
}): void {
  eventStore = options.store;
  onEventStoreError = options.onError ?? null;
}

export function configureKpiMinimumSampleSize(value: number): void {
  minimumSampleSize = Math.max(1, Math.floor(value));
}

function applyKpiEvent(event: PersistedKpiEvent, addNotification = true): void {
  recentEvents.push(event);
  if (recentEvents.length > maxRecentEvents) {
    recentEvents.splice(0, recentEvents.length - maxRecentEvents);
  }

  const { metric, status, timestamp } = event;
  const current = stats.get(metric) ?? {
    metric,
    section: event.section,
    total: 0,
    success: 0,
    failure: 0,
    lastSeenAt: timestamp,
    totalDurationMs: 0
  };

  current.total += 1;
  if (status === "success") {
    current.success += 1;
  } else {
    current.failure += 1;
  }

  const elapsed = typeof event.elapsedMs === "number" ? event.elapsedMs : 0;
  current.totalDurationMs += elapsed;
  current.avgDurationMs =
    current.total > 0 ? Math.round(current.totalDurationMs / current.total) : 0;
  current.rating = computeKpiRating({
    successRate: current.total > 0 ? (current.success / current.total) * 100 : 0,
    failureCount: current.failure,
    total: current.total,
    thresholds: resolveThresholdsForMetric(metric, current.section)
  });
  current.lastSeenAt = timestamp;
  const provider = event.provider;
  if (provider) {
    current.lastProvider = provider;
  }
  if (status === "failure") {
    current.lastFailureAt = current.lastSeenAt;
    if (event.errorClass) {
      current.lastErrorClass = event.errorClass;
    }
    current.lastError = readFailureReason(event);
    if (addNotification) {
      recentFailures.unshift({
        id: `${metric}:${current.lastSeenAt}:${current.failure}`,
        timestamp: current.lastSeenAt,
        metric,
        section: current.section,
        title: metric,
        reason: current.lastError,
        ...(current.lastErrorClass ? { errorClass: current.lastErrorClass } : {}),
        ...(provider ? { provider } : {}),
        ...(event.sessionId ? { sessionId: event.sessionId } : {}),
        rating: current.rating
      });
      recentFailures.splice(maxRecentFailures);
    }
  }

  stats.set(metric, current);
}

export function getKpiSummary(): {
  generatedAt: string;
  minimumSampleSize: number;
  metrics: KpiMetricScore[];
} {
  const metrics = [...stats.values()]
    .map((entry) => ({
      ...entry,
      successRate:
        entry.total > 0
          ? Number(((entry.success / entry.total) * 100).toFixed(2))
          : 0,
      rating:
        entry.rating ??
        computeKpiRating({
          successRate:
            entry.total > 0 ? (entry.success / entry.total) * 100 : 0,
          failureCount: entry.failure,
          total: entry.total,
          thresholds: resolveThresholdsForMetric(entry.metric, entry.section)
        }),
      sampleStatus: resolveSampleStatus(entry.total),
      minimumSampleSize
    }))
    .sort((a, b) => a.metric.localeCompare(b.metric));

  return {
    generatedAt: new Date().toISOString(),
    minimumSampleSize,
    metrics
  };
}

export function resetKpiSummary(): void {
  stats.clear();
  recentFailures.length = 0;
  recentEvents.length = 0;
}

export function getKpiScorecard(): KpiScorecard {
  return buildKpiScorecard(false);
}

export function getFocusedKpiScorecard(): KpiScorecard {
  return buildKpiScorecard(true);
}

export function getFilteredKpiScorecard(
  filters: ScorecardFilters
): ReturnType<typeof getKpiScorecard> {
  return buildKpiScorecard(false, filters);
}

export function getFilteredFocusedKpiScorecard(
  filters: ScorecardFilters
): ReturnType<typeof getFocusedKpiScorecard> {
  return buildKpiScorecard(true, filters);
}

export async function getProviderKpiDiagnostics(filters?: {
  window?: ScorecardWindow;
}): Promise<{
  generatedAt: string;
  window: ScorecardWindow;
  providers: Array<{
    provider: string;
    total: number;
    success: number;
    failure: number;
    successRate: number;
    avgLatencyMs: number | null;
    p95LatencyMs: number | null;
    p99LatencyMs: number | null;
    topErrors: Array<{ reason: string; count: number }>;
  }>;
}> {
  const window = filters?.window ?? "today";
  if (!eventStore) {
    const summaries = getKpiSummary().metrics.filter(
      (metric) =>
        metric.metric.startsWith("provider.") &&
        metric.metric.endsWith(".health") &&
        typeof metric.lastProvider === "string" &&
        metric.lastProvider.trim().length > 0
    );
    const providers = summaries.map((metric) => {
      const failures = recentFailures.filter(
        (failure) => failure.provider === metric.lastProvider
      );
      const errorCounts = new Map<string, number>();
      for (const failure of failures) {
        errorCounts.set(failure.reason, (errorCounts.get(failure.reason) ?? 0) + 1);
      }
      return {
        provider: metric.lastProvider!,
        total: metric.total,
        success: metric.success,
        failure: metric.failure,
        successRate: metric.successRate,
        avgLatencyMs: metric.avgDurationMs ?? null,
        p95LatencyMs: null,
        p99LatencyMs: null,
        topErrors: [...errorCounts.entries()]
          .sort((a, b) => b[1] - a[1])
          .slice(0, 3)
          .map(([reason, count]) => ({ reason, count }))
      };
    });
    return {
      generatedAt: new Date().toISOString(),
      window,
      providers
    };
  }

  const events = await readWindowedEvents(window);
  const providerEvents = events.filter(
    (event) =>
      event.metric.startsWith("provider.") &&
      event.metric.endsWith(".health") &&
      typeof event.provider === "string" &&
      event.provider.trim().length > 0
  );
  const groups = new Map<string, PersistedKpiEvent[]>();
  for (const event of providerEvents) {
    const key = `${event.provider}`.trim();
    const bucket = groups.get(key) ?? [];
    bucket.push(event);
    groups.set(key, bucket);
  }

  const providers = [...groups.entries()]
    .map(([provider, entries]) => {
      const total = entries.length;
      const success = entries.filter((entry) => entry.status === "success").length;
      const failure = total - success;
      const latencies = entries
        .map((entry) => entry.elapsedMs)
        .filter((value): value is number => typeof value === "number" && value >= 0)
        .sort((a, b) => a - b);
      const errors = new Map<string, number>();
      for (const entry of entries) {
        if (entry.status !== "failure") {
          continue;
        }
        const key = (entry.error || entry.errorClass || "unknown").trim();
        errors.set(key, (errors.get(key) ?? 0) + 1);
      }
      return {
        provider,
        total,
        success,
        failure,
        successRate: total > 0 ? Number(((success / total) * 100).toFixed(2)) : 0,
        avgLatencyMs:
          latencies.length > 0
            ? Math.round(latencies.reduce((sum, ms) => sum + ms, 0) / latencies.length)
            : null,
        p95LatencyMs: percentile(latencies, 0.95),
        p99LatencyMs: percentile(latencies, 0.99),
        topErrors: [...errors.entries()]
          .sort((a, b) => b[1] - a[1])
          .slice(0, 3)
          .map(([reason, count]) => ({ reason, count }))
      };
    })
    .sort((a, b) => {
      if (a.successRate !== b.successRate) {
        return a.successRate - b.successRate;
      }
      return b.total - a.total;
    });

  return {
    generatedAt: new Date().toISOString(),
    window,
    providers
  };
}

export async function getPipelineKpiDiagnostics(filters?: {
  window?: ScorecardWindow;
}): Promise<{
  generatedAt: string;
  window: ScorecardWindow;
  stages: Array<{
    metric: string;
    rating: KpiRating;
    total: number;
    success: number;
    failure: number;
    successRate: number;
    avgDurationMs: number | null;
    lastSeenAt: string;
    lastFailureAt?: string;
    lastError?: string;
  }>;
}> {
  const window = filters?.window ?? "today";
  const summary = await readWindowedSummary(window);
  const pipelineMetrics = [
    "session.transcription.queue",
    "session.transcription.provider_call",
    "session.transcription.end_to_end",
    "session.report_context.upload",
    "report_context.preprocess",
    "report_context.provider_call",
    "report_context.extract_parse",
    "session.report_context.extract",
    "session.transcription.stale_recovery",
    "session.transcription.dead_letter",
    "fettle_sync.queued",
    "fettle_sync.attempt",
    "fettle_sync.success",
    "fettle_sync.failure",
    "fettle_sync.dead_letter",
    "fettle_sync.stale_recovery"
  ];
  const byMetric = new Map(summary.map((entry) => [entry.metric, entry]));
  const stages = pipelineMetrics.map((metric) => {
    const found = byMetric.get(metric);
    if (!found) {
      return {
        metric,
        rating: "good" as KpiRating,
        total: 0,
        success: 0,
        failure: 0,
        successRate: 0,
        avgDurationMs: null,
        lastSeenAt: ""
      };
    }
    return {
      metric,
      rating: found.rating,
      total: found.total,
      success: found.success,
      failure: found.failure,
      successRate: found.successRate,
      avgDurationMs: found.avgDurationMs ?? null,
      lastSeenAt: found.lastSeenAt,
      ...(found.lastFailureAt ? { lastFailureAt: found.lastFailureAt } : {}),
      ...(found.lastError ? { lastError: found.lastError } : {})
    };
  });

  return {
    generatedAt: new Date().toISOString(),
    window,
    stages
  };
}

export async function getReportContextFallbackDiagnostics(filters?: {
  window?: ScorecardWindow;
}): Promise<{
  generatedAt: string;
  window: ScorecardWindow;
  summary: {
    total: number;
    fallbackUsed: number;
    primaryPath: number;
    failure: number;
    success: number;
    fallbackRate: number;
  };
  byOperation: Array<{
    operation: string;
    total: number;
    fallbackUsed: number;
    primaryPath: number;
    failure: number;
    success: number;
    fallbackRate: number;
  }>;
  trend: Array<{
    bucket: string;
    fallbackUsed: number;
    primaryPath: number;
    total: number;
    fallbackRate: number;
  }>;
}> {
  const window = filters?.window ?? "today";
  const events = await readWindowedEvents(window);
  const fallbackEvents = events.filter(
    (event) =>
      event.metric === "report_context.fallback_path"
      && typeof event.fallbackUsed === "boolean"
  );

  const summary = summarizeFallbackEvents(fallbackEvents);
  const operationMap = new Map<string, PersistedKpiEvent[]>();
  for (const event of fallbackEvents) {
    const operation = typeof event.operation === "string" && event.operation.trim().length > 0
      ? event.operation.trim()
      : "unknown";
    const bucket = operationMap.get(operation) ?? [];
    bucket.push(event);
    operationMap.set(operation, bucket);
  }

  const byOperation = [...operationMap.entries()]
    .map(([operation, operationEvents]) => ({
      operation,
      ...summarizeFallbackEvents(operationEvents)
    }))
    .sort((a, b) => {
      if (a.fallbackRate !== b.fallbackRate) {
        return b.fallbackRate - a.fallbackRate;
      }
      return b.total - a.total;
    });

  const trendMap = new Map<string, PersistedKpiEvent[]>();
  for (const event of fallbackEvents) {
    const bucket = toHourlyBucket(event.timestamp);
    const bucketEvents = trendMap.get(bucket) ?? [];
    bucketEvents.push(event);
    trendMap.set(bucket, bucketEvents);
  }

  const trend = [...trendMap.entries()]
    .sort(([a], [b]) => a.localeCompare(b))
    .map(([bucket, bucketEvents]) => ({
      bucket,
      ...summarizeFallbackEvents(bucketEvents)
    }));

  return {
    generatedAt: new Date().toISOString(),
    window,
    summary,
    byOperation,
    trend
  };
}

export async function getKpiEventTrendDiagnostics(filters?: {
  window?: ScorecardWindow;
}): Promise<{
  generatedAt: string;
  window: ScorecardWindow;
  points: Array<{
    bucket: string;
    totalEvents: number;
    failures: number;
    successRate: number;
  }>;
  stageLatency: Array<{
    metric: string;
    points: Array<{
      bucket: string;
      avgLatencyMs: number;
      samples: number;
    }>;
  }>;
}> {
  const window = filters?.window ?? "today";
  const events = await readWindowedEvents(window);
  const ordered = [...events].sort((a, b) => a.timestamp.localeCompare(b.timestamp));

  const trafficMap = new Map<string, PersistedKpiEvent[]>();
  for (const event of ordered) {
    const bucket = toHourlyBucket(event.timestamp);
    const list = trafficMap.get(bucket) ?? [];
    list.push(event);
    trafficMap.set(bucket, list);
  }

  const points = [...trafficMap.entries()]
    .sort(([a], [b]) => a.localeCompare(b))
    .map(([bucket, entries]) => {
      const totalEvents = entries.length;
      const failures = entries.filter((entry) => entry.status === "failure").length;
      const success = totalEvents - failures;
      return {
        bucket,
        totalEvents,
        failures,
        successRate: totalEvents > 0 ? Number(((success / totalEvents) * 100).toFixed(2)) : 0
      };
    });

  const latencyMetrics = [
    "session.transcription.queue",
    "session.transcription.provider_call",
    "session.transcription.end_to_end",
    "report_context.preprocess",
    "report_context.provider_call",
    "report_context.extract_parse",
    "fettle_sync.attempt",
    "fettle_sync.success"
  ];
  const stageLatency = latencyMetrics.map((metric) => {
    const metricEvents = ordered.filter(
      (event) => event.metric === metric && typeof event.elapsedMs === "number"
    );
    const byBucket = new Map<string, number[]>();
    for (const event of metricEvents) {
      const bucket = toHourlyBucket(event.timestamp);
      const list = byBucket.get(bucket) ?? [];
      list.push(event.elapsedMs as number);
      byBucket.set(bucket, list);
    }
    return {
      metric,
      points: [...byBucket.entries()]
        .sort(([a], [b]) => a.localeCompare(b))
        .map(([bucket, samples]) => ({
          bucket,
          avgLatencyMs: Math.round(samples.reduce((sum, value) => sum + value, 0) / samples.length),
          samples: samples.length
        }))
    };
  });

  return {
    generatedAt: new Date().toISOString(),
    window,
    points,
    stageLatency
  };
}

function buildKpiScorecard(
  focusMode: boolean,
  filters?: ScorecardFilters
): KpiScorecard {
  let summary = getKpiSummary().metrics;
  let notifications = recentFailures;
  if (filters?.window && filters.window !== "all_time") {
    const scopedWindow: "today" | "yesterday" | "this_week" | "this_month" =
      filters.window;
    summary = summary.filter((metric) =>
      isInWindowLocal(metric.lastSeenAt, scopedWindow)
    );
    notifications = notifications.filter((notification) =>
      isInWindowLocal(notification.timestamp, scopedWindow)
    );
  }
  if (filters?.includeExpected) {
    summary = includeExpectedMetrics(summary);
  }
  if (filters?.metricContains) {
    const needle = filters.metricContains.trim().toLowerCase();
    summary = summary.filter((metric) =>
      metric.metric.toLowerCase().includes(needle)
    );
  }
  const grouped = new Map<string, Array<(typeof summary)[number]>>();

  for (const metric of summary) {
    const key = metric.section;
    const sectionMetrics = grouped.get(key) ?? [];
    sectionMetrics.push(metric);
    grouped.set(key, sectionMetrics);
  }

  const sections = [...grouped.entries()]
    .filter(([section]) => {
      if (!filters?.section) {
        return true;
      }
      return section === filters.section;
    })
    .map(([section, metrics]) => {
    const sortedMetrics = [...metrics];
    if (focusMode) {
      sortedMetrics.sort((a, b) => {
        const ratingDiff = getRatingWeight(b.rating) - getRatingWeight(a.rating);
        if (ratingDiff !== 0) {
          return ratingDiff;
        }
        return a.successRate - b.successRate;
      });
    }
    const total = metrics.reduce((sum, metric) => sum + metric.total, 0);
    const success = metrics.reduce((sum, metric) => sum + metric.success, 0);
    const failure = metrics.reduce((sum, metric) => sum + metric.failure, 0);
    const successRate =
      total > 0 ? Number(((success / total) * 100).toFixed(2)) : 0;
    const rating = aggregateSectionRating(metrics.map((metric) => metric.rating));
    const sampleStatus = resolveSampleStatus(total);

      return {
      section,
      rating,
      sampleStatus,
      successRate,
      total,
      success,
      failure,
      metrics: sortedMetrics
      };
    })
    .filter((section) => {
      if (!filters?.rating) {
        return true;
      }
      return section.rating === filters.rating;
    });

  sections.sort((a, b) => {
    if (focusMode) {
      const ratingDiff = getRatingWeight(b.rating) - getRatingWeight(a.rating);
      if (ratingDiff !== 0) {
        return ratingDiff;
      }
      return a.successRate - b.successRate;
    }
    return a.section.localeCompare(b.section);
  });
  return {
    generatedAt: new Date().toISOString(),
    minimumSampleSize,
    notifications,
    sections
  };
}

function includeExpectedMetrics(
  summary: KpiMetricScore[]
): KpiMetricScore[] {
  const byMetric = new Map(summary.map((entry) => [entry.metric, entry]));
  for (const metric of expectedPipelineMetrics) {
    if (byMetric.has(metric)) {
      continue;
    }
    const section = extractSection(metric);
    byMetric.set(metric, {
      metric,
      section,
      total: 0,
      success: 0,
      failure: 0,
      successRate: 0,
      lastSeenAt: "",
      totalDurationMs: 0,
      avgDurationMs: 0,
      rating: "good",
      sampleStatus: "no_data",
      minimumSampleSize
    });
  }
  return [...byMetric.values()];
}

function resolveSampleStatus(total: number): KpiSampleStatus {
  if (total <= 0) {
    return "no_data";
  }
  if (total < minimumSampleSize) {
    return "low_sample";
  }
  return "ready";
}

function readFailureReason(event: {
  error?: string;
  errorClass?: string;
}): string {
  if (typeof event.error === "string" && event.error.trim()) {
    return event.error.trim();
  }
  if (typeof event.errorClass === "string" && event.errorClass.trim()) {
    return event.errorClass.trim();
  }
  return "The step reported a failure without a detailed reason.";
}

async function persistKpiEvent(event: PersistedKpiEvent): Promise<void> {
  if (!eventStore) {
    return;
  }
  try {
    await eventStore.append(event);
  } catch (error) {
    onEventStoreError?.(error);
  }
}

function extractSection(metric: string): string {
  const [firstSegment] = metric.split(".");
  return firstSegment?.trim() || "other";
}

function getRatingWeight(rating: KpiRating): number {
  switch (rating) {
    case "critical":
      return 4;
    case "risky":
      return 3;
    case "good":
      return 2;
    case "excellent":
      return 1;
  }
}

function isInWindowLocal(
  timestamp: string,
  window: "today" | "yesterday" | "this_week" | "this_month"
): boolean {
  if (!timestamp) {
    return false;
  }
  const instant = new Date(timestamp);
  if (Number.isNaN(instant.getTime())) {
    return false;
  }
  const now = new Date();
  const startOfToday = new Date(
    now.getFullYear(),
    now.getMonth(),
    now.getDate()
  );
  const startOfTomorrow = new Date(
    now.getFullYear(),
    now.getMonth(),
    now.getDate() + 1
  );

  if (window === "today") {
    return instant >= startOfToday && instant < startOfTomorrow;
  }

  if (window === "yesterday") {
    const startOfYesterday = new Date(
      now.getFullYear(),
      now.getMonth(),
      now.getDate() - 1
    );
    return instant >= startOfYesterday && instant < startOfToday;
  }

  if (window === "this_week") {
    const day = now.getDay();
    const mondayOffset = day === 0 ? -6 : 1 - day;
    const startOfWeek = new Date(
      now.getFullYear(),
      now.getMonth(),
      now.getDate() + mondayOffset
    );
    return instant >= startOfWeek && instant < startOfTomorrow;
  }

  const startOfMonth = new Date(now.getFullYear(), now.getMonth(), 1);
  return instant >= startOfMonth && instant < startOfTomorrow;
}

async function readWindowedEvents(
  window: ScorecardWindow
): Promise<PersistedKpiEvent[]> {
  if (!eventStore) {
    if (window === "all_time") {
      return [...recentEvents];
    }
    return recentEvents.filter((event) => isInWindowLocal(event.timestamp, window));
  }
  if (window === "all_time") {
    const since = new Date(Date.now() - 365 * 24 * 60 * 60 * 1000).toISOString();
    return eventStore.listSince(since);
  }
  return eventStore.listSince(resolveWindowStart(window).toISOString());
}

async function readWindowedSummary(
  window: ScorecardWindow
): Promise<Array<KpiStat & { successRate: number; rating: KpiRating }>> {
  if (!eventStore || window === "all_time") {
    return getKpiSummary().metrics;
  }
  const events = await readWindowedEvents(window);
  const transient = new Map<string, KpiStat>();
  for (const event of events) {
    const current = transient.get(event.metric) ?? {
      metric: event.metric,
      section: event.section,
      total: 0,
      success: 0,
      failure: 0,
      lastSeenAt: event.timestamp,
      totalDurationMs: 0
    };
    current.total += 1;
    if (event.status === "success") {
      current.success += 1;
    } else {
      current.failure += 1;
      current.lastFailureAt = event.timestamp;
      if (event.errorClass) {
        current.lastErrorClass = event.errorClass;
      }
      const failureReason = event.error || event.errorClass;
      if (failureReason) {
        current.lastError = failureReason;
      }
    }
    const elapsed = typeof event.elapsedMs === "number" ? event.elapsedMs : 0;
    current.totalDurationMs += elapsed;
    current.avgDurationMs = current.total > 0
      ? Math.round(current.totalDurationMs / current.total)
      : 0;
    current.lastSeenAt = event.timestamp;
    transient.set(event.metric, current);
  }

  return [...transient.values()].map((entry) => ({
    ...entry,
    successRate:
      entry.total > 0
        ? Number(((entry.success / entry.total) * 100).toFixed(2))
        : 0,
    rating: computeKpiRating({
      successRate: entry.total > 0 ? (entry.success / entry.total) * 100 : 0,
      failureCount: entry.failure,
      total: entry.total,
      thresholds: resolveThresholdsForMetric(entry.metric, entry.section)
    })
  }));
}

function resolveWindowStart(
  window: Exclude<ScorecardWindow, "all_time">
): Date {
  const now = new Date();
  const startOfToday = new Date(now.getFullYear(), now.getMonth(), now.getDate());
  if (window === "today") {
    return startOfToday;
  }
  if (window === "yesterday") {
    return new Date(now.getFullYear(), now.getMonth(), now.getDate() - 1);
  }
  if (window === "this_week") {
    const day = now.getDay();
    const mondayOffset = day === 0 ? -6 : 1 - day;
    return new Date(now.getFullYear(), now.getMonth(), now.getDate() + mondayOffset);
  }
  return new Date(now.getFullYear(), now.getMonth(), 1);
}

function percentile(sorted: number[], p: number): number | null {
  if (sorted.length === 0) {
    return null;
  }
  const rank = Math.ceil(p * sorted.length) - 1;
  const index = Math.min(sorted.length - 1, Math.max(0, rank));
  return sorted[index] ?? null;
}

function summarizeFallbackEvents(events: PersistedKpiEvent[]): {
  total: number;
  fallbackUsed: number;
  primaryPath: number;
  failure: number;
  success: number;
  fallbackRate: number;
} {
  const total = events.length;
  const fallbackUsed = events.filter((event) => event.fallbackUsed === true).length;
  const primaryPath = total - fallbackUsed;
  const failure = events.filter((event) => event.status === "failure").length;
  const success = total - failure;
  return {
    total,
    fallbackUsed,
    primaryPath,
    failure,
    success,
    fallbackRate: total > 0 ? Number(((fallbackUsed / total) * 100).toFixed(2)) : 0
  };
}

function toHourlyBucket(timestamp: string): string {
  const instant = new Date(timestamp);
  if (Number.isNaN(instant.getTime())) {
    return "invalid-time";
  }
  const year = instant.getFullYear();
  const month = String(instant.getMonth() + 1).padStart(2, "0");
  const day = String(instant.getDate()).padStart(2, "0");
  const hour = String(instant.getHours()).padStart(2, "0");
  return `${year}-${month}-${day} ${hour}:00`;
}
