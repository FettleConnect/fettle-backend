import { Router } from "express";

import {
  getFocusedKpiScorecard,
  getFilteredFocusedKpiScorecard,
  getFilteredKpiScorecard,
  getKpiEventTrendDiagnostics,
  getKpiSummary,
  getPipelineKpiDiagnostics,
  getProviderKpiDiagnostics,
  getReportContextFallbackDiagnostics,
  getKpiScorecard
} from "../kpi-registry.js";
import { parseWindowFilter } from "../http-helpers.js";

export function createKpiRouter(): Router {
  const router = Router();

  router.get("/public/reliability-kpis", (_request, response) => {
    response.json(getKpiSummary());
  });

  router.get("/public/kpi/scorecard", (request, response) => {
    const focus = `${request.query.focus ?? ""}`.trim().toLowerCase();
    const focusMode = focus === "1" || focus === "true" || focus === "yes";
    const includeExpectedQuery = `${request.query.includeExpected ?? ""}`.trim().toLowerCase();
    const includeExpected =
      includeExpectedQuery === "1" ||
      includeExpectedQuery === "true" ||
      includeExpectedQuery === "yes";
    const todayOnlyQuery = `${request.query.today ?? ""}`.trim().toLowerCase();
    const filterToday =
      todayOnlyQuery === "1" ||
      todayOnlyQuery === "true" ||
      todayOnlyQuery === "yes";
    const windowRaw = `${request.query.window ?? ""}`.trim().toLowerCase();
    const windowFilter = parseWindowFilter(windowRaw);
    const section = `${request.query.section ?? ""}`.trim();
    const ratingRaw = `${request.query.rating ?? ""}`.trim().toLowerCase();
    const metricContains = `${request.query.metricContains ?? ""}`.trim();
    const rating: "excellent" | "good" | "risky" | "critical" | undefined = (
      ratingRaw === "excellent" ||
      ratingRaw === "good" ||
      ratingRaw === "risky" ||
      ratingRaw === "critical"
    )
      ? ratingRaw
      : undefined;

    if (!includeExpected && !filterToday && !windowFilter && !section && !rating && !metricContains) {
      response.json(focusMode ? getFocusedKpiScorecard() : getKpiScorecard());
      return;
    }

    const filters: {
      includeExpected?: boolean;
      window?: "today" | "yesterday" | "this_week" | "this_month" | "all_time";
      section?: string;
      rating?: "excellent" | "good" | "risky" | "critical";
      metricContains?: string;
    } = {};
    if (includeExpected) {
      filters.includeExpected = true;
    }
    if (windowFilter) {
      filters.window = windowFilter;
    } else if (filterToday) {
      filters.window = "today";
    }
    if (section) {
      filters.section = section;
    }
    if (rating) {
      filters.rating = rating;
    }
    if (metricContains) {
      filters.metricContains = metricContains;
    }
    response.json(
      focusMode
        ? getFilteredFocusedKpiScorecard(filters)
        : getFilteredKpiScorecard(filters)
    );
  });

  router.get("/public/kpi/providers", async (request, response, next) => {
    try {
      const windowFilter = parseWindowFilter(`${request.query.window ?? ""}`);
      const payload = await getProviderKpiDiagnostics({
        window: windowFilter ?? "today"
      });
      response.json(payload);
    } catch (error) {
      next(error);
    }
  });

  router.get("/public/kpi/pipeline", async (request, response, next) => {
    try {
      const windowFilter = parseWindowFilter(`${request.query.window ?? ""}`);
      const payload = await getPipelineKpiDiagnostics({
        window: windowFilter ?? "today"
      });
      response.json(payload);
    } catch (error) {
      next(error);
    }
  });

  router.get("/public/kpi/report-context-fallback", async (request, response, next) => {
    try {
      const windowFilter = parseWindowFilter(`${request.query.window ?? ""}`);
      const payload = await getReportContextFallbackDiagnostics({
        window: windowFilter ?? "today"
      });
      response.json(payload);
    } catch (error) {
      next(error);
    }
  });

  router.get("/public/kpi/event-trends", async (request, response, next) => {
    try {
      const windowFilter = parseWindowFilter(`${request.query.window ?? ""}`);
      const payload = await getKpiEventTrendDiagnostics({
        window: windowFilter ?? "today"
      });
      response.json(payload);
    } catch (error) {
      next(error);
    }
  });

  return router;
}
