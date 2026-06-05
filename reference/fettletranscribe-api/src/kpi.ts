import { logInfo, type LogContext } from "./logger.js";
import { recordKpi } from "./kpi-registry.js";

export type KpiStatus = "success" | "failure";

export function emitKpi(
  metric: string,
  status: KpiStatus,
  context: LogContext = {},
  options?: { scope?: string }
): void {
  recordKpi(metric, status, context);
  logInfo(
    "kpi_event",
    {
      metric,
      status,
      ...context
    },
    { scope: options?.scope ?? "kpi" }
  );
}
