const DEFAULT_MAX_FAILURES = 3;
const DEFAULT_COOLDOWN_MS = 30_000;

interface CircuitState {
  consecutiveFailures: number;
  openedAt?: number;
}

const circuitRegistry = new Map<string, CircuitState>();

export class CircuitOpenError extends Error {
  readonly operation: string;
  readonly retryAfterMs: number;

  constructor(operation: string, retryAfterMs: number) {
    super(`Circuit is open for ${operation}. Retry after ${retryAfterMs}ms.`);
    this.name = "CircuitOpenError";
    this.operation = operation;
    this.retryAfterMs = retryAfterMs;
  }
}

export interface ReliabilityPolicy {
  operation: string;
  timeoutMs: number;
  maxRetries: number;
  retryDelayMs: number;
  maxFailuresBeforeOpen?: number;
  cooldownMs?: number;
  shouldRetry?: (error: unknown) => boolean;
}

export async function runWithReliability<T>(
  task: () => Promise<T>,
  policy: ReliabilityPolicy
): Promise<T> {
  assertCircuitClosed(policy);

  let lastError: unknown;

  for (let attempt = 1; attempt <= policy.maxRetries + 1; attempt += 1) {
    try {
      const result = await withTimeout(task(), policy.timeoutMs, policy.operation);
      markSuccess(policy.operation);
      return result;
    } catch (error) {
      lastError = error;
      const retryable = policy.shouldRetry ? policy.shouldRetry(error) : true;
      const isLastAttempt = attempt >= policy.maxRetries + 1;

      if (!retryable || isLastAttempt) {
        markFailure(policy);
        throw error;
      }

      await delay(policy.retryDelayMs);
    }
  }

  markFailure(policy);
  throw lastError;
}

function assertCircuitClosed(policy: ReliabilityPolicy): void {
  const state = circuitRegistry.get(policy.operation);

  if (!state?.openedAt) {
    return;
  }

  const cooldownMs = policy.cooldownMs ?? DEFAULT_COOLDOWN_MS;
  const elapsed = Date.now() - state.openedAt;

  if (elapsed >= cooldownMs) {
    circuitRegistry.set(policy.operation, { consecutiveFailures: 0 });
    return;
  }

  throw new CircuitOpenError(policy.operation, cooldownMs - elapsed);
}

function markSuccess(operation: string): void {
  circuitRegistry.set(operation, { consecutiveFailures: 0 });
}

function markFailure(policy: ReliabilityPolicy): void {
  const state = circuitRegistry.get(policy.operation) ?? { consecutiveFailures: 0 };
  const nextFailures = state.consecutiveFailures + 1;
  const maxFailures = policy.maxFailuresBeforeOpen ?? DEFAULT_MAX_FAILURES;

  if (nextFailures >= maxFailures) {
    circuitRegistry.set(policy.operation, {
      consecutiveFailures: nextFailures,
      openedAt: Date.now()
    });
    return;
  }

  circuitRegistry.set(policy.operation, { consecutiveFailures: nextFailures });
}

function withTimeout<T>(
  promise: Promise<T>,
  timeoutMs: number,
  operation: string
): Promise<T> {
  if (timeoutMs <= 0) {
    return promise;
  }

  return new Promise<T>((resolve, reject) => {
    const timeout = setTimeout(() => {
      reject(new Error(`${operation} timed out after ${timeoutMs}ms.`));
    }, timeoutMs);

    promise.then(
      (value) => {
        clearTimeout(timeout);
        resolve(value);
      },
      (error) => {
        clearTimeout(timeout);
        reject(error);
      }
    );
  });
}

function delay(durationMs: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, durationMs));
}

export function resetReliabilityState(): void {
  circuitRegistry.clear();
}
