import { type Request } from "express";
import { ZodError, type ZodIssue } from "zod";

import {
  appVersion,
  healthCheckResponseSchema,
  type DoctorProfile,
  type HealthCheckResponse
} from "@medivoice/shared";

import { NotFoundError, UnauthorizedError } from "./errors.js";
import { type AuthService } from "./auth-service.js";

export function createHealthPayload(): HealthCheckResponse {
  return healthCheckResponseSchema.parse({
    status: "ok",
    service: "api",
    timestamp: new Date().toISOString(),
    version: appVersion
  });
}

export function formatZodErrorMessage(error: ZodError): string {
  const firstIssue = error.issues[0];

  if (!firstIssue) {
    return "Please review the highlighted fields and try again.";
  }

  return formatIssueMessage(firstIssue);
}

export function parseWindowFilter(
  raw: string
): "today" | "yesterday" | "this_week" | "this_month" | "all_time" | undefined {
  const normalized = raw.trim().toLowerCase();
  return normalized === "today" ||
    normalized === "yesterday" ||
    normalized === "this_week" ||
    normalized === "this_month" ||
    normalized === "all_time"
    ? normalized
    : undefined;
}

export async function requireDoctor(
  request: Request,
  authService: AuthService
): Promise<DoctorProfile> {
  const token = readBearerToken(request);

  if (!token) {
    throw new UnauthorizedError("Please log in to continue.");
  }

  try {
    return await authService.getDoctorFromToken(token);
  } catch (error) {
    if (error instanceof NotFoundError) {
      throw new UnauthorizedError("Please log in to continue.");
    }

    throw error;
  }
}

export async function requireDoctorFromStreamRequest(
  request: Request,
  authService: AuthService
): Promise<DoctorProfile> {
  const bearerToken = readBearerToken(request);
  const queryToken =
    typeof request.query.access_token === "string"
      ? request.query.access_token.trim()
      : "";
  const token = bearerToken ?? (queryToken.length > 0 ? queryToken : null);

  if (!token) {
    throw new UnauthorizedError("Please log in to continue.");
  }

  try {
    return await authService.getDoctorFromToken(token);
  } catch (error) {
    if (error instanceof NotFoundError) {
      throw new UnauthorizedError("Please log in to continue.");
    }

    throw error;
  }
}

function readBearerToken(request: Request): string | null {
  const header = request.header("authorization")?.trim();

  if (!header?.toLowerCase().startsWith("bearer ")) {
    return null;
  }

  const token = header.slice("bearer ".length).trim();
  return token.length > 0 ? token : null;
}

function formatIssueMessage(issue: ZodIssue): string {
  const label = formatIssuePath(issue.path);

  if (issue.code === "too_small" && issue.minimum === 1) {
    return `${label} is required.`;
  }

  if (
    issue.code === "too_small" &&
    typeof issue.minimum === "number" &&
    issue.origin === "string"
  ) {
    if (label.toLowerCase().includes("mobile number")) {
      return `${label} must be at least ${issue.minimum} digits.`;
    }

    return `${label} must be at least ${issue.minimum} characters.`;
  }

  if (
    issue.code === "too_big" &&
    typeof issue.maximum === "number" &&
    issue.origin === "string"
  ) {
    return `${label} must be no more than ${issue.maximum} characters.`;
  }

  if (issue.code === "invalid_format" && label === "Session ID") {
    return "The consultation session could not be found.";
  }

  if (
    issue.code === "invalid_format" &&
    label.toLowerCase().includes("mobile number")
  ) {
    return `${label} must be exactly 10 digits.`;
  }

  return `${label}: ${issue.message}`;
}

function formatIssuePath(path: ReadonlyArray<PropertyKey>): string {
  if (path.length === 0) {
    return "Request";
  }

  const normalizedPath = path
    .map((segment) => segment.toString())
    .filter((segment) => segment.length > 0)
    .join(".");

  switch (normalizedPath) {
    case "patient.name":
      return "Patient name";
    case "patient.phoneNumber":
      return "Patient mobile number";
    case "doctor.name":
      return "Doctor name";
    case "doctor.phoneNumber":
      return "Doctor mobile number";
    case "fileName":
      return "Audio file name";
    case "mimeType":
      return "Audio format";
    case "durationSeconds":
      return "Audio duration";
    case "data":
      return "Audio recording";
    default:
      return startCase(normalizedPath.replace(/\.\d+/g, ""));
  }
}

function startCase(value: string): string {
  return value
    .split(".")
    .filter(Boolean)
    .map((segment) =>
      segment
        .replace(/([a-z])([A-Z])/g, "$1 $2")
        .replace(/[_-]+/g, " ")
        .trim()
    )
    .join(" ")
    .replace(/^\w/, (character) => character.toUpperCase());
}
