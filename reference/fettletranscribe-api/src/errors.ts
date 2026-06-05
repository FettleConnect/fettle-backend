import type { AuthErrorCode } from "@medivoice/shared";

export class NotFoundError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "NotFoundError";
  }
}

export class UnauthorizedError extends Error {
  constructor(
    message: string,
    readonly code?: AuthErrorCode
  ) {
    super(message);
    this.name = "UnauthorizedError";
  }
}

export class ConflictError extends Error {
  constructor(
    message: string,
    readonly code?: AuthErrorCode
  ) {
    super(message);
    this.name = "ConflictError";
  }
}

export class LockedError extends Error {
  constructor(
    message: string,
    readonly code?: AuthErrorCode
  ) {
    super(message);
    this.name = "LockedError";
  }
}

export class BadRequestError extends Error {
  constructor(
    message: string,
    readonly code?: AuthErrorCode
  ) {
    super(message);
    this.name = "BadRequestError";
  }
}

export class FeatureNotReadyError extends Error {
  constructor(
    message: string,
    readonly code?: AuthErrorCode
  ) {
    super(message);
    this.name = "FeatureNotReadyError";
  }
}

export class InvalidSessionStateError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "InvalidSessionStateError";
  }
}

export class AIProviderError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "AIProviderError";
  }
}

export class MalformedAIOutputError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "MalformedAIOutputError";
  }
}
