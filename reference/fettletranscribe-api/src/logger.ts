type LogLevel = "debug" | "info" | "warn" | "error";

type LogValue = string | number | boolean | null | undefined;

export type LogContext = Record<string, LogValue>;

interface SerializedError {
  name: string;
  message: string;
  stack?: string;
}

interface LogEntry {
  level: LogLevel;
  event: string;
  timestamp: string;
  service: "api";
  scope: string;
  errorClass?: string;
  error?: SerializedError;
}

const levelOrder: Record<LogLevel, number> = {
  debug: 10,
  info: 20,
  warn: 30,
  error: 40
};

function getConfiguredLogLevel(): LogLevel {
  const configured = process.env.LOG_LEVEL?.trim().toLowerCase();
  if (configured === "debug" || configured === "info" || configured === "warn" || configured === "error") {
    return configured;
  }
  return "info";
}

function shouldLog(level: LogLevel): boolean {
  return levelOrder[level] >= levelOrder[getConfiguredLogLevel()];
}

export function classifyError(error: unknown): string {
  if (error instanceof Error) {
    const name = error.name.toLowerCase();
    if (name.includes("zod") || name.includes("badrequest")) {
      return "validation";
    }
    if (name.includes("unauthorized")) {
      return "auth";
    }
    if (name.includes("notfound")) {
      return "not_found";
    }
    if (name.includes("conflict") || name.includes("invalidsessionstate")) {
      return "state_conflict";
    }
    if (name.includes("aiprovider")) {
      return "dependency_ai_provider";
    }
    if (name.includes("abort")) {
      return "canceled";
    }
  }
  return "unexpected";
}

export function logDebug(
  event: string,
  context: LogContext = {},
  options?: { scope?: string }
): void {
  writeLog("debug", event, context, options);
}

export function logInfo(
  event: string,
  context: LogContext = {},
  options?: { scope?: string }
): void {
  writeLog("info", event, context, options);
}

export function logWarn(
  event: string,
  context: LogContext = {},
  options?: { scope?: string; error?: unknown }
): void {
  writeLog("warn", event, context, options);
}

export function logError(
  event: string,
  context: LogContext = {},
  options?: { scope?: string; error?: unknown }
): void {
  writeLog("error", event, context, options);
}

function writeLog(
  level: LogLevel,
  event: string,
  context: LogContext,
  options?: { scope?: string; error?: unknown }
): void {
  if (!shouldLog(level)) {
    return;
  }

  const entry: LogEntry & LogContext = {
    level,
    event,
    timestamp: new Date().toISOString(),
    service: "api",
    scope: options?.scope ?? "app",
    ...context
  };

  if (options?.error !== undefined) {
    const serializedError = serializeError(options.error);
    entry.errorClass = classifyError(options.error);
    if (serializedError) {
      entry.error = serializedError;
    }
  }

  const message = safeJsonStringify(entry);

  if (level === "error") {
    console.error(message);
    return;
  }

  if (level === "warn") {
    console.warn(message);
    return;
  }

  if (level === "debug") {
    console.debug(message);
    return;
  }

  console.info(message);
}

function serializeError(error: unknown): SerializedError | undefined {
  if (error instanceof Error) {
    return {
      name: error.name,
      message: error.message,
      ...(error.stack ? { stack: error.stack } : {})
    };
  }

  return {
    name: "NonError",
    message:
      typeof error === "string"
        ? error
        : safeJsonStringify(error, "Unserializable non-Error value")
  };
}

function safeJsonStringify(value: unknown, fallback = "null"): string {
  const seen = new WeakSet<object>();

  const serialized = JSON.stringify(value, (_key, candidate: unknown) => {
    if (typeof candidate === "bigint") {
      return candidate.toString();
    }

    if (typeof candidate === "object" && candidate !== null) {
      if (seen.has(candidate)) {
        return "[Circular]";
      }

      seen.add(candidate);
    }

    return candidate;
  });

  return serialized ?? fallback;
}
