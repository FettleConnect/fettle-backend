import { afterEach, describe, expect, it, vi } from "vitest";

import {
  CircuitOpenError,
  resetReliabilityState,
  runWithReliability
} from "./reliability.js";

afterEach(() => {
  resetReliabilityState();
  vi.restoreAllMocks();
});

describe("runWithReliability", () => {
  it("retries transient failures and succeeds", async () => {
    let attempts = 0;

    const result = await runWithReliability(
      async () => {
        attempts += 1;
        if (attempts < 3) {
          throw new Error("temporary");
        }
        return "ok";
      },
      {
        operation: "test-op",
        timeoutMs: 1_000,
        maxRetries: 2,
        retryDelayMs: 1,
        shouldRetry: () => true
      }
    );

    expect(result).toBe("ok");
    expect(attempts).toBe(3);
  });

  it("opens circuit after repeated failures", async () => {
    await expect(
      runWithReliability(
        async () => {
          throw new Error("boom");
        },
        {
          operation: "test-circuit",
          timeoutMs: 1_000,
          maxRetries: 0,
          retryDelayMs: 1,
          maxFailuresBeforeOpen: 1
        }
      )
    ).rejects.toThrow("boom");

    await expect(
      runWithReliability(
        async () => "ok",
        {
          operation: "test-circuit",
          timeoutMs: 1_000,
          maxRetries: 0,
          retryDelayMs: 1,
          maxFailuresBeforeOpen: 1,
          cooldownMs: 60_000
        }
      )
    ).rejects.toBeInstanceOf(CircuitOpenError);
  });
});
