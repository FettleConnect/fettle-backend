import { type Request } from "express";

import { BadRequestError } from "./errors.js";
import { logWarn } from "./logger.js";

export function readReportMimeType(request: Request): string {
  return request.header("content-type")?.split(";")[0]?.trim() || "application/octet-stream";
}

export function readReportFileName(request: Request): string {
  const header = request.header("x-report-file-name")?.trim();

  if (header && header.length > 0) {
    return header.replace(/[\/\\:\0\r\n]/g, "_");
  }

  return `session-${request.params.sessionId}-report`;
}

function readAudioMimeType(request: Request): string {
  return request.header("content-type")?.split(";")[0]?.trim() || "application/octet-stream";
}

function readAudioFileName(request: Request): string {
  const header = request.header("x-audio-file-name")?.trim();

  if (header && header.length > 0) {
    return header.replace(/[\/\\:\0\r\n]/g, "_");
  }

  return `session-${request.params.sessionId}.webm`;
}

function readAudioDurationSeconds(request: Request): number {
  const header = request.header("x-audio-duration-seconds")?.trim();

  if (!header) {
    return 0;
  }

  const parsed = Number(header);
  if (!Number.isFinite(parsed)) {
    return 0;
  }
  return parsed >= 0 ? parsed : Number.NaN;
}

export function buildCanonicalAudioUploadMetadata(
  request: Request,
  data: Buffer
): {
  fileName: string;
  mimeType: string;
  durationSeconds: number;
} {
  const fileName = readAudioFileName(request);
  const headerMimeType = readAudioMimeType(request);
  const durationSeconds = readAudioDurationSeconds(request);
  const detectedMimeType = detectAudioMimeTypeFromBytes(data);
  const mimeType = detectedMimeType ?? headerMimeType;

  if (detectedMimeType && detectedMimeType !== headerMimeType) {
    logWarn(
      "audio_upload_metadata_mismatch",
      {
        sessionId: `${request.params.sessionId}`,
        headerMimeType,
        detectedMimeType,
        fileName,
        sizeBytes: data.length
      },
      { scope: "http" }
    );
  }

  if (durationSeconds === 0) {
    logWarn(
      "audio_upload_duration_missing_or_invalid",
      {
        sessionId: `${request.params.sessionId}`,
        fileName,
        mimeType
      },
      { scope: "http" }
    );
  }

  return {
    fileName,
    mimeType,
    durationSeconds
  };
}

function detectAudioMimeTypeFromBytes(data: Buffer): string | null {
  if (data.length < 12) {
    return null;
  }

  if (
    data[0] === 0x1a &&
    data[1] === 0x45 &&
    data[2] === 0xdf &&
    data[3] === 0xa3
  ) {
    return "audio/webm";
  }

  if (data[0] === 0x4f && data[1] === 0x67 && data[2] === 0x67 && data[3] === 0x53) {
    return "audio/ogg";
  }

  const firstByte = data[0] ?? -1;
  const secondByte = data[1] ?? -1;
  if (
    data.slice(0, 3).toString("ascii") === "ID3" ||
    (data.length > 1 && firstByte === 0xff && (secondByte & 0xe0) === 0xe0)
  ) {
    return "audio/mpeg";
  }

  if (data.slice(0, 4).toString("ascii") === "RIFF" && data.slice(8, 12).toString("ascii") === "WAVE") {
    return "audio/wav";
  }

  if (data.slice(4, 8).toString("ascii") === "ftyp") {
    return "audio/mp4";
  }

  return null;
}

export function readIdempotencyKey(request: Request): string | undefined {
  const header = request.header("x-idempotency-key")?.trim();
  if (!header) {
    return undefined;
  }
  if (header.length < 8) {
    throw new BadRequestError("X-Idempotency-Key must be at least 8 characters.");
  }
  return header.slice(0, 128);
}
