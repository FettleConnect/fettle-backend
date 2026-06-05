import { afterEach, describe, expect, it, vi } from "vitest";

import { logDebug, logError } from "./logger.js";

describe("logger", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("serializes circular non-Error values without throwing", () => {
    const consoleError = vi
      .spyOn(console, "error")
      .mockImplementation(() => undefined);
    const circular: { self?: unknown } = {};
    circular.self = circular;

    expect(() => {
      logError("request_failed", {}, { error: circular });
    }).not.toThrow();

    expect(consoleError).toHaveBeenCalledWith(
      expect.stringContaining('"message":"{\\"self\\":\\"[Circular]\\"}"')
    );
  });

  it("serializes bigint values without throwing", () => {
    const consoleError = vi
      .spyOn(console, "error")
      .mockImplementation(() => undefined);

    expect(() => {
      logError("request_failed", { requestId: "123" }, { error: 42n });
    }).not.toThrow();

    expect(consoleError).toHaveBeenCalledWith(
      expect.stringContaining('"message":"\\"42\\""')
    );
  });

  it("respects LOG_LEVEL and suppresses debug logs by default", () => {
    const originalLevel = process.env.LOG_LEVEL;
    const consoleDebug = vi
      .spyOn(console, "debug")
      .mockImplementation(() => undefined);
    try {
      delete process.env.LOG_LEVEL;
      logDebug("debug_event", { requestId: "abc" });
      expect(consoleDebug).not.toHaveBeenCalled();
    } finally {
      if (originalLevel === undefined) {
        delete process.env.LOG_LEVEL;
      } else {
        process.env.LOG_LEVEL = originalLevel;
      }
    }
  });

  it("adds service and errorClass fields to error logs", () => {
    const consoleError = vi
      .spyOn(console, "error")
      .mockImplementation(() => undefined);

    logError("request_failed", { requestId: "r-1" }, { error: new Error("boom") });

    expect(consoleError).toHaveBeenCalledWith(
      expect.stringContaining('"service":"api"')
    );
    expect(consoleError).toHaveBeenCalledWith(
      expect.stringContaining('"errorClass":"unexpected"')
    );
  });
});
