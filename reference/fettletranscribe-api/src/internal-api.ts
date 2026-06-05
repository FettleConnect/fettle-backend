import { timingSafeEqual } from "node:crypto";

import { type Request } from "express";

import { UnauthorizedError } from "./errors.js";

export interface InternalApiConfig {
  enabled: boolean;
  apiKeys: string[];
}

export interface InternalApiActor {
  authenticationMethod: "authorization" | "x-internal-api-key";
  caller?: string;
  adminUserId?: string;
  adminUserEmail?: string;
}

interface InternalApiCredential {
  source: InternalApiActor["authenticationMethod"];
  value: string;
}

export function isInternalApiEnabled(config: InternalApiConfig): boolean {
  return config.enabled && config.apiKeys.length > 0;
}

export function requireInternalApi(
  request: Request,
  config: InternalApiConfig
): InternalApiActor {
  if (!isInternalApiEnabled(config)) {
    throw new UnauthorizedError("Unauthorized internal request.");
  }

  const credential = readInternalApiCredential(request);

  if (!credential || !matchesInternalApiKey(credential.value, config.apiKeys)) {
    throw new UnauthorizedError("Unauthorized internal request.");
  }

  const caller = readOptionalHeader(request, "x-internal-caller");
  const adminUserId = readOptionalHeader(request, "x-admin-user-id");
  const adminUserEmail = readOptionalHeader(request, "x-admin-user-email");

  return {
    authenticationMethod: credential.source,
    ...(caller ? { caller } : {}),
    ...(adminUserId ? { adminUserId } : {}),
    ...(adminUserEmail ? { adminUserEmail } : {})
  };
}

function readInternalApiCredential(
  request: Request
): InternalApiCredential | null {
  const authorization = request.header("authorization")?.trim();

  if (authorization?.toLowerCase().startsWith("bearer ")) {
    const bearerToken = authorization.slice("bearer ".length).trim();

    if (bearerToken.length > 0) {
      return {
        source: "authorization",
        value: bearerToken
      };
    }
  }

  const apiKey = readOptionalHeader(request, "x-internal-api-key");

  if (apiKey) {
    return {
      source: "x-internal-api-key",
      value: apiKey
    };
  }

  return null;
}

function matchesInternalApiKey(
  candidate: string,
  apiKeys: string[]
): boolean {
  const candidateBuffer = Buffer.from(candidate);

  return apiKeys.some((apiKey) => {
    const keyBuffer = Buffer.from(apiKey);

    return (
      candidateBuffer.length === keyBuffer.length &&
      timingSafeEqual(candidateBuffer, keyBuffer)
    );
  });
}

function readOptionalHeader(
  request: Request,
  headerName: string
): string | undefined {
  const value = request.header(headerName)?.trim();
  return value ? value : undefined;
}
