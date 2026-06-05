import { execFile } from "node:child_process";
import { mkdtemp, readFile, readdir, rm, stat, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { basename, extname, join } from "node:path";

import { AIProviderError } from "./errors.js";
import { logWarn } from "./logger.js";

const MIN_CHUNK_DURATION_SECONDS = 120;
const MIN_RETRY_SEGMENT_SECONDS = 30;
const MAX_SPLIT_ATTEMPTS = 4;

export interface StoredSessionAudio {
  fileName: string;
  mimeType: string;
  durationSeconds: number;
  data: Buffer;
}

export async function splitAudioForTranscription(
  audio: StoredSessionAudio,
  options: {
    maxChunkBytes: number;
    targetChunkBytes: number;
    signal?: AbortSignal;
  }
): Promise<StoredSessionAudio[]> {
  const sourceDuration = Math.max(1, Math.round(audio.durationSeconds || 1));
  const estimatedFromBitrate = Math.floor(
    (options.targetChunkBytes / Math.max(1, audio.data.length)) * sourceDuration
  );
  const estimatedSegmentSeconds = Math.max(
    1,
    sourceDuration <= MIN_CHUNK_DURATION_SECONDS
      ? estimatedFromBitrate
      : Math.max(MIN_CHUNK_DURATION_SECONDS, estimatedFromBitrate)
  );
  const tempDir = await mkdtemp(join(tmpdir(), "medivoice-audio-chunks-"));
  const sourceName = sanitizeFileName(audio.fileName, "consultation.webm");
  const sourceExt = extname(sourceName) || ".webm";
  const sourceBaseName = basename(sourceName, sourceExt);
  const sourcePath = join(tempDir, `source${sourceExt}`);

  try {
    await writeFile(sourcePath, audio.data);
    let segmentSeconds = estimatedSegmentSeconds;
    let fileNames = await splitWithFfmpegSegments(
      tempDir,
      sourcePath,
      sourceExt,
      segmentSeconds,
      options.signal
    );
    let attempts = 1;
    while (attempts < MAX_SPLIT_ATTEMPTS) {
      const oversizedFound = await hasOversizedChunk(
        tempDir,
        fileNames,
        options.maxChunkBytes
      );

      if (!oversizedFound) {
        break;
      }

      const nextSegmentSeconds = Math.max(
        MIN_RETRY_SEGMENT_SECONDS,
        Math.floor(segmentSeconds / 2)
      );
      if (nextSegmentSeconds >= segmentSeconds) {
        break;
      }

      segmentSeconds = nextSegmentSeconds;
      attempts += 1;
      fileNames = await splitWithFfmpegSegments(
        tempDir,
        sourcePath,
        sourceExt,
        segmentSeconds,
        options.signal
      );
    }

    if (fileNames.length === 0) {
      throw new AIProviderError(
        "Unable to split long consultation audio for transcription."
      );
    }

    const chunks: StoredSessionAudio[] = [];
    for (const name of fileNames) {
      const path = join(tempDir, name);
      const data = await readFile(path);

      if (data.length > options.maxChunkBytes) {
        throw new AIProviderError(
          "Long consultation audio could not be split below the transcription provider limit."
        );
      }

      const fallbackDurationSeconds = Math.max(
        1,
        Math.min(segmentSeconds, sourceDuration)
      );
      chunks.push({
        fileName: `${sourceBaseName}-${name}`,
        mimeType: audio.mimeType || "audio/webm",
        durationSeconds: await probeDurationSeconds(
          path,
          fallbackDurationSeconds,
          options.signal
        ),
        data
      });
    }

    return chunks;
  } catch (error) {
    if (isAbortErrorLike(error)) {
      throw error;
    }

    if (error instanceof AIProviderError) {
      throw error;
    }

    logWarn(
      "transcription_chunking_unavailable",
      {
        fileName: audio.fileName,
        mimeType: audio.mimeType
      },
      {
        scope: "transcription",
        error
      }
    );

    throw new AIProviderError(
      "Long consultation transcription requires ffmpeg support on the API runtime."
    );
  } finally {
    await rm(tempDir, { recursive: true, force: true });
  }
}

export async function normalizeAudioForOpenAI(
  audio: StoredSessionAudio,
  signal?: AbortSignal
): Promise<StoredSessionAudio> {
  const tempDir = await mkdtemp(join(tmpdir(), "medivoice-openai-audio-"));
  const sourceName = sanitizeFileName(audio.fileName, "consultation.webm");
  const sourceExt = extname(sourceName) || ".webm";
  const sourceBaseName = basename(sourceName, sourceExt);
  const sourcePath = join(tempDir, `source${sourceExt}`);
  const outputPath = join(tempDir, `${sourceBaseName || "consultation"}-openai.mp3`);

  try {
    await writeFile(sourcePath, audio.data);
    await runCommand(
      "ffmpeg",
      [
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-i",
        sourcePath,
        "-vn",
        "-ac",
        "1",
        "-ar",
        "16000",
        "-codec:a",
        "libmp3lame",
        "-b:a",
        "64k",
        outputPath
      ],
      signal
    );

    const data = await readFile(outputPath);
    return {
      fileName: basename(outputPath),
      mimeType: "audio/mpeg",
      durationSeconds: await probeDurationSeconds(
        outputPath,
        audio.durationSeconds,
        signal
      ),
      data
    };
  } catch (error) {
    if (isAbortErrorLike(error)) {
      throw error;
    }

    logWarn(
      "openai_audio_normalization_failed",
      {
        fileName: audio.fileName,
        mimeType: audio.mimeType,
        audioSizeBytes: audio.data.length,
        audioDurationSeconds: Math.round(audio.durationSeconds)
      },
      {
        scope: "transcription",
        error
      }
    );

    return audio;
  } finally {
    await rm(tempDir, { recursive: true, force: true });
  }
}

export function buildOpenAITranscriptionForm(
  audio: StoredSessionAudio,
  model: string
): FormData {
  const form = new FormData();
  form.append("model", model);
  form.append("response_format", "diarized_json");
  form.append("chunking_strategy", "auto");
  form.append(
    "file",
    new Blob([audio.data], {
      type: audio.mimeType || "audio/webm"
    }),
    audio.fileName || "consultation.webm"
  );
  return form;
}

export function delay(ms: number, signal?: AbortSignal): Promise<void> {
  return new Promise<void>((resolve, reject) => {
    if (signal?.aborted) {
      reject(createAbortError());
      return;
    }

    const timeout = setTimeout(() => {
      signal?.removeEventListener("abort", handleAbort);
      resolve();
    }, ms);

    function handleAbort() {
      clearTimeout(timeout);
      reject(createAbortError());
    }

    signal?.addEventListener("abort", handleAbort, { once: true });
  });
}

export function isAbortErrorLike(error: unknown): boolean {
  return error instanceof Error && error.name === "AbortError";
}

function createAbortError(): DOMException {
  return new DOMException("The operation was aborted.", "AbortError");
}

function sanitizeFileName(value: string, fallback: string): string {
  const normalized = value.trim().replace(/[^\w.-]+/g, "_");
  return normalized.length > 0 ? normalized : fallback;
}

async function splitWithFfmpegSegments(
  tempDir: string,
  sourcePath: string,
  sourceExt: string,
  segmentSeconds: number,
  signal?: AbortSignal
): Promise<string[]> {
  await clearChunkFiles(tempDir, sourceExt);
  const chunkPattern = join(tempDir, `chunk-%03d${sourceExt}`);
  await runCommand(
    "ffmpeg",
    [
      "-hide_banner",
      "-loglevel",
      "error",
      "-y",
      "-i",
      sourcePath,
      "-c",
      "copy",
      "-f",
      "segment",
      "-segment_time",
      String(segmentSeconds),
      "-reset_timestamps",
      "1",
      chunkPattern
    ],
    signal
  );

  return (await readdir(tempDir))
    .filter((name) => name.startsWith("chunk-") && name.endsWith(sourceExt))
    .sort();
}

async function clearChunkFiles(
  tempDir: string,
  sourceExt: string
): Promise<void> {
  const fileNames = (await readdir(tempDir))
    .filter((name) => name.startsWith("chunk-") && name.endsWith(sourceExt));
  await Promise.all(
    fileNames.map((name) => rm(join(tempDir, name), { force: true }))
  );
}

async function hasOversizedChunk(
  tempDir: string,
  fileNames: string[],
  maxChunkBytes: number
): Promise<boolean> {
  for (const fileName of fileNames) {
    const { size } = await stat(join(tempDir, fileName));
    if (size > maxChunkBytes) {
      return true;
    }
  }

  return false;
}

async function probeDurationSeconds(
  path: string,
  fallbackSeconds: number,
  signal?: AbortSignal
): Promise<number> {
  try {
    const output = await runCommand(
      "ffprobe",
      [
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "default=noprint_wrappers=1:nokey=1",
        path
      ],
      signal
    );
    const parsed = Number.parseFloat(output.trim());
    return Number.isFinite(parsed) && parsed > 0 ? parsed : fallbackSeconds;
  } catch {
    return fallbackSeconds;
  }
}

async function runCommand(
  command: string,
  args: string[],
  signal?: AbortSignal
): Promise<string> {
  return new Promise<string>((resolve, reject) => {
    execFile(command, args, { signal }, (error, stdout, stderr) => {
      if (error) {
        if (isAbortErrorLike(error)) {
          reject(error);
          return;
        }
        const message = stderr.trim() || error.message;
        reject(new Error(message));
        return;
      }

      resolve(stdout);
    });
  });
}
