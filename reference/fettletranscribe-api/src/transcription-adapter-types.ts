import {
  type ConsultationMetadata,
  type DraftConsultationDocument,
  type TranscriptSegment,
  consultationMetadataSchema,
  draftConsultationDocumentSchema,
  transcriptSegmentSchema
} from "@medivoice/shared";
import { z } from "zod";

import {
  normalizeAudioForOpenAI,
  splitAudioForTranscription,
  type StoredSessionAudio
} from "./transcription-audio-utils.js";

export type { StoredSessionAudio } from "./transcription-audio-utils.js";

export const MAX_GEMINI_INLINE_AUDIO_BYTES = 15_000_000;
export const MAX_OPENAI_AUDIO_BYTES = 24_000_000;
export const TARGET_CHUNK_AUDIO_BYTES = 12_000_000;
export const SHARED_CHUNK_TRANSCRIBE_MAX_BYTES = MAX_GEMINI_INLINE_AUDIO_BYTES;
export const PROVIDER_ERROR_BODY_MAX_CHARS = 4000;
export const OPENAI_TRANSCRIPTION_MAX_ATTEMPTS = 2;
export const OPENAI_TRANSCRIPTION_RETRY_DELAY_MS = 1000;
export const GEMINI_TRANSCRIPTION_MAX_ATTEMPTS = 3;
export const GEMINI_TRANSCRIPTION_RETRY_DELAY_MS = 1000;
export const SYNTHESIS_JSON_REPAIR_MAX_ATTEMPTS = 2;
export const CHUNK_TRANSCRIPTION_MAX_ATTEMPTS = 3;
export const CHUNK_BOUNDARY_WINDOW_SECONDS = 18;

export const geminiTranscriptionPrompt = `
You are generating a structured medical consultation transcription result.
The consultation audio may contain English, Hindi, Telugu, or mixed-language speech.
For transcriptSegments.text, preserve the spoken wording but transliterate Indian-language speech into readable Latin script (for example, Hinglish or Tenglish in English letters) instead of native script where possible.
Do not translate transcriptSegments into formal English unless the speaker already spoke in English.
Write summary, metadata, and draftDocument in clear clinical English.
Return JSON only with this exact top-level shape:
{
  "summary": string,
  "transcriptSegments": [
    {
      "speaker": "doctor" | "patient" | "unknown",
      "speakerLabel": string,
      "text": string,
      "startedAtSeconds": number,
      "endedAtSeconds": number
    }
  ],
  "metadata": {
    "chiefComplaint"?: string,
    "symptoms": string[],
    "diagnoses": string[],
    "followUpPlan"?: string,
    "careTeamNotes": string[]
  },
  "draftDocument": {
    "title": string,
    "chiefComplaint"?: string,
    "illnessHistory"?: string,
    "clinicalSummary": string,
    "medications": [
      {
        "medicationName": string,
        "dosage": string,
        "instructions": string
      }
    ],
    "testsPrescribed": string[],
    "revisitDate"?: string,
    "nextSteps": string[],
    "cautions": string[]
  }
}
Do not wrap the JSON in markdown fences.
Use empty arrays instead of null.
If any investigations, labs, scans, or imaging are advised, put them in draftDocument.testsPrescribed.
Put a value in draftDocument.revisitDate only when the conversation explicitly mentions a follow-up date or timing. Leave it empty for generic advice like "review if needed" or "return if symptoms worsen".
draftDocument.revisitDate must be strict ISO format YYYY-MM-DD or omitted.
Today (local reference date): {{TODAY_ISO_DATE}}.
For draftDocument.illnessHistory, write 2-5 short bullet-style clinical points separated by newlines. Keep each point concise and avoid paragraph format.
`.trim();

export const geminiTranscriptSynthesisPrompt = `
You are generating a final structured medical consultation output from an existing transcript.
Write summary, metadata, and draftDocument in clear clinical English.
Return JSON only with this exact top-level shape:
{
  "summary": string,
  "metadata": {
    "chiefComplaint"?: string,
    "symptoms": string[],
    "diagnoses": string[],
    "followUpPlan"?: string,
    "careTeamNotes": string[]
  },
  "draftDocument": {
    "title": string,
    "chiefComplaint"?: string,
    "illnessHistory"?: string,
    "clinicalSummary": string,
    "medications": [
      {
        "medicationName": string,
        "dosage": string,
        "instructions": string
      }
    ],
    "testsPrescribed": string[],
    "revisitDate"?: string,
    "nextSteps": string[],
    "cautions": string[]
  }
}
Do not wrap JSON in markdown fences.
Use empty arrays instead of null.
Do not invent facts not present in the transcript.
If any investigations, labs, scans, or imaging are advised, put them in draftDocument.testsPrescribed.
Put a value in draftDocument.revisitDate only when the conversation explicitly mentions a follow-up date or timing. Leave it empty for generic advice like "review if needed" or "return if symptoms worsen".
draftDocument.revisitDate must be strict ISO format YYYY-MM-DD or omitted.
Today (local reference date): {{TODAY_ISO_DATE}}.
For draftDocument.illnessHistory, write 2-5 short bullet-style clinical points separated by newlines. Keep each point concise and avoid paragraph format.
`.trim();

export const openAITranscriptSynthesisPrompt = geminiTranscriptSynthesisPrompt;

export const transcriptSegmentDraftSchema = z
  .object({
    speaker: transcriptSegmentSchema.shape.speaker,
    speakerLabel: transcriptSegmentSchema.shape.speakerLabel,
    text: transcriptSegmentSchema.shape.text,
    startedAtSeconds: transcriptSegmentSchema.shape.startedAtSeconds,
    endedAtSeconds: transcriptSegmentSchema.shape.endedAtSeconds
  })
  .refine(
    (segment) => segment.endedAtSeconds >= segment.startedAtSeconds,
    {
      message:
        "endedAtSeconds must be greater than or equal to startedAtSeconds"
    }
  );

export const adapterResponseSchema = z.object({
  summary: z.string().trim().min(1),
  transcriptSegments: z.array(transcriptSegmentDraftSchema),
  metadata: consultationMetadataSchema,
  draftDocument: draftConsultationDocumentSchema
});

export type AdapterResponse = z.infer<typeof adapterResponseSchema>;

export interface TranscriptionContext {
  patientName: string;
  doctorName: string;
}

export interface TranscriptionAdapterInput {
  sessionId: string;
  audio: StoredSessionAudio;
  context: TranscriptionContext;
  executionMode?: "interactive" | "background";
  signal?: AbortSignal;
}

export interface TranscriptionAdapterResult {
  provider: string;
  requiresRecovery: boolean;
  summary: string;
  transcriptSegments: TranscriptSegment[];
  metadata: ConsultationMetadata;
  draftDocument: DraftConsultationDocument;
}

export interface TranscriptionAdapter {
  readonly providerName: string;
  readonly maxAudioBytes?: number;
  transcribe(
    input: TranscriptionAdapterInput
  ): Promise<TranscriptionAdapterResult>;
}

export interface GeminiAdapterOptions {
  apiKey: string;
  model?: string;
  baseUrl?: string;
  fetchImpl?: typeof fetch;
  splitAudioImpl?: typeof splitAudioForTranscription;
}

export interface OpenAIAdapterOptions {
  apiKey: string;
  transcribeModel?: string;
  synthesisModel?: string;
  baseUrl?: string;
  fetchImpl?: typeof fetch;
  splitAudioImpl?: typeof splitAudioForTranscription;
  normalizeAudioImpl?: typeof normalizeAudioForOpenAI;
}

export interface FallbackAdapterOptions {
  primary: TranscriptionAdapter;
  fallback: TranscriptionAdapter;
}
