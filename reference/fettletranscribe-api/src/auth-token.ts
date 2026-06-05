import { createHmac, timingSafeEqual } from "node:crypto";

import { type DoctorProfile } from "@medivoice/shared";

interface AuthTokenPayload {
  sub: string;
  name: string;
  phoneNumber?: string;
  email?: string;
  tokenVersion: number;
  purpose?: "doctor" | "pin_change";
  exp: number;
}

export function issueDoctorToken(
  doctor: DoctorProfile & { tokenVersion?: number },
  secret: string,
  expiresInSeconds = 30 * 24 * 60 * 60
): string {
  const payload: AuthTokenPayload = {
    sub: doctor.id,
    name: doctor.name,
    ...(doctor.phoneNumber ? { phoneNumber: doctor.phoneNumber } : {}),
    ...(doctor.email ? { email: doctor.email } : {}),
    tokenVersion: doctor.tokenVersion ?? 0,
    exp: Math.floor(Date.now() / 1000) + expiresInSeconds
  };

  return signToken(payload, secret);
}

export function issuePinChangeToken(
  doctor: DoctorProfile & { tokenVersion?: number },
  secret: string,
  expiresInSeconds = 10 * 60
): string {
  return signToken(
    {
      sub: doctor.id,
      name: doctor.name,
      ...(doctor.phoneNumber ? { phoneNumber: doctor.phoneNumber } : {}),
      ...(doctor.email ? { email: doctor.email } : {}),
      tokenVersion: doctor.tokenVersion ?? 0,
      purpose: "pin_change",
      exp: Math.floor(Date.now() / 1000) + expiresInSeconds
    },
    secret
  );
}

export function verifyDoctorToken(
  token: string,
  secret: string
): { doctorId: string; tokenVersion: number } | null {
  const payload = verifyToken(token, secret);

  if (!payload) {
    return null;
  }

  if (payload.exp <= Math.floor(Date.now() / 1000)) {
    return null;
  }

  if (payload.purpose && payload.purpose !== "doctor") {
    return null;
  }

  return {
    doctorId: payload.sub,
    tokenVersion: payload.tokenVersion ?? 0
  };
}

export function verifyPinChangeToken(
  token: string,
  secret: string
): { doctorId: string; tokenVersion: number } | null {
  const payload = verifyToken(token, secret);

  if (!payload || payload.exp <= Math.floor(Date.now() / 1000)) {
    return null;
  }

  if (payload.purpose !== "pin_change") {
    return null;
  }

  return {
    doctorId: payload.sub,
    tokenVersion: payload.tokenVersion ?? 0
  };
}

function signToken(payload: AuthTokenPayload, secret: string): string {
  const header = {
    alg: "HS256",
    typ: "JWT"
  };
  const encodedHeader = encodeBase64Url(JSON.stringify(header));
  const encodedPayload = encodeBase64Url(JSON.stringify(payload));
  const signature = createSignature(
    `${encodedHeader}.${encodedPayload}`,
    secret
  );

  return `${encodedHeader}.${encodedPayload}.${signature}`;
}

function verifyToken(
  token: string,
  secret: string
): AuthTokenPayload | null {
  const [encodedHeader, encodedPayload, signature] = token.split(".");

  if (!encodedHeader || !encodedPayload || !signature) {
    return null;
  }

  const expectedSignature = createSignature(
    `${encodedHeader}.${encodedPayload}`,
    secret
  );

  if (!safeEqual(signature, expectedSignature)) {
    return null;
  }

  try {
    return JSON.parse(decodeBase64Url(encodedPayload)) as AuthTokenPayload;
  } catch {
    return null;
  }
}

function createSignature(value: string, secret: string): string {
  return createHmac("sha256", secret).update(value).digest("base64url");
}

function encodeBase64Url(value: string): string {
  return Buffer.from(value, "utf8").toString("base64url");
}

function decodeBase64Url(value: string): string {
  return Buffer.from(value, "base64url").toString("utf8");
}

function safeEqual(left: string, right: string): boolean {
  const leftBuffer = Buffer.from(left, "utf8");
  const rightBuffer = Buffer.from(right, "utf8");
  const expectedLength = rightBuffer.length;
  const paddedLeft = Buffer.alloc(expectedLength);

  leftBuffer.copy(paddedLeft, 0, 0, Math.min(leftBuffer.length, expectedLength));

  const lengthsMatch = leftBuffer.length === rightBuffer.length;
  const contentsMatch = timingSafeEqual(paddedLeft, rightBuffer);
  return lengthsMatch && contentsMatch;
}
