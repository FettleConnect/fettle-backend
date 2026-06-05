import { AIProviderError, MalformedAIOutputError } from "./errors.js";
import {
  type AdapterResponse,
  PROVIDER_ERROR_BODY_MAX_CHARS,
  type TranscriptionAdapterInput
} from "./transcription-adapter-types.js";
export function normalizeOpenAITranscriptForSynthesis(
  segments: AdapterResponse["transcriptSegments"],
  input: TranscriptionAdapterInput
): AdapterResponse["transcriptSegments"] {
  const speakerStats = collectSpeakerStats(segments);

  return segments.map((segment) => {
    const speaker = inferOpenAISpeakerRole(segment, input, speakerStats);
    const normalizedLabel = normalizeSpeakerDisplayLabel(
      segment.speakerLabel,
      speaker,
      input
    );

    return {
      ...segment,
      speaker,
      speakerLabel: normalizedLabel,
      text: normalizeIndicScriptTranscriptText(segment.text)
    };
  });
}

export function postProcessTranscriptSegments(
  segments: AdapterResponse["transcriptSegments"],
  input: TranscriptionAdapterInput
): AdapterResponse["transcriptSegments"] {
  const normalized = segments.map((segment) => ({
    ...segment,
    speakerLabel: normalizeSpeakerDisplayLabel(
      segment.speakerLabel,
      segment.speaker,
      input
    ),
    text: normalizeIndicScriptTranscriptText(segment.text)
  }));
  return enforceSpeakerConsistency(normalized, input);
}

function enforceSpeakerConsistency(
  segments: AdapterResponse["transcriptSegments"],
  input: TranscriptionAdapterInput
): AdapterResponse["transcriptSegments"] {
  if (segments.length === 0) {
    return segments;
  }

  const scored = segments.map((segment) => ({
    segment,
    doctorScore: scoreDoctorEvidence(segment, input),
    patientScore: scorePatientEvidence(segment, input)
  }));

  return scored.map(({ segment, doctorScore, patientScore }) => {
    const normalizedText = segment.text.toLowerCase();
    const strongPatientSymptomTurn = hasStrongPatientSymptomSignal(normalizedText);
    const doctorDirectiveTurn = hasDoctorDirectiveSignal(normalizedText);

    if (strongPatientSymptomTurn && !doctorDirectiveTurn) {
      return {
        ...segment,
        speaker: "patient" as const,
        speakerLabel: input.context.patientName.trim()
      };
    }

    const contradictoryPatientLabel =
      segment.speaker === "patient" &&
      (hasStrongDoctorSpeechSignal(normalizedText) ||
        (doctorScore >= 2 && doctorScore > patientScore));
    if (contradictoryPatientLabel) {
      return {
        ...segment,
        speaker: "doctor" as const,
        speakerLabel: input.context.doctorName.trim()
      };
    }

    const contradictoryDoctorLabel =
      segment.speaker === "doctor" &&
      patientScore >= 2 &&
      patientScore > doctorScore &&
      hasPatientSpeechSignal(segment.text.toLowerCase());
    if (contradictoryDoctorLabel) {
      return {
        ...segment,
        speaker: "patient" as const,
        speakerLabel: input.context.patientName.trim()
      };
    }

    if (segment.speaker === "unknown") {
      if (doctorScore >= 2 && doctorScore > patientScore) {
        return {
          ...segment,
          speaker: "doctor" as const,
          speakerLabel: input.context.doctorName.trim()
        };
      }
      if (patientScore >= 2 && patientScore > doctorScore) {
        return {
          ...segment,
          speaker: "patient" as const,
          speakerLabel: input.context.patientName.trim()
        };
      }
    }

    const hasStrongOwnEvidence =
      segment.speaker === "doctor"
        ? doctorScore >= 2 || doctorScore > patientScore
        : segment.speaker === "patient"
          ? patientScore >= 2 || patientScore > doctorScore
          : false;
    if (hasStrongOwnEvidence) {
      return segment;
    }

    return segment;
  }).map((segment, index, all) => {
    if (index === 0 || index === all.length - 1) {
      return segment;
    }
    const previous = all[index - 1];
    const next = all[index + 1];
    if (!previous || !next || previous.speaker !== next.speaker) {
      return segment;
    }

    // Prefer alternation smoothing when neighbors agree and current turn is weak/ambiguous.
    if (segment.speaker === "unknown") {
      return {
        ...segment,
        speaker: previous.speaker,
        speakerLabel:
          previous.speaker === "doctor"
            ? input.context.doctorName.trim()
            : input.context.patientName.trim()
      };
    }

    const currentText = segment.text.toLowerCase();
    const hasStrongPatient = hasStrongPatientSymptomSignal(currentText);
    const hasDoctorDirective = hasDoctorDirectiveSignal(currentText);
    if (
      segment.speaker === "doctor" &&
      previous.speaker === "patient" &&
      hasStrongPatient &&
      !hasDoctorDirective
    ) {
      return {
        ...segment,
        speaker: "patient",
        speakerLabel: input.context.patientName.trim()
      };
    }
    return segment;
  });
}

function scoreDoctorEvidence(
  segment: AdapterResponse["transcriptSegments"][number],
  input: TranscriptionAdapterInput
): number {
  let score = 0;
  const label = segment.speakerLabel.toLowerCase();
  const text = segment.text.toLowerCase();
  if (label.includes("doctor") || label.includes("dr.")) {
    score += 2;
  }
  if (label.includes(input.context.doctorName.toLowerCase())) {
    score += 3;
  }
  if (hasDoctorSpeechSignal(text)) {
    score += 1;
  }
  if (hasStrongDoctorSpeechSignal(text)) {
    score += 2;
  }
  return score;
}

function scorePatientEvidence(
  segment: AdapterResponse["transcriptSegments"][number],
  input: TranscriptionAdapterInput
): number {
  let score = 0;
  const label = segment.speakerLabel.toLowerCase();
  const text = segment.text.toLowerCase();
  if (label.includes("patient")) {
    score += 2;
  }
  if (label.includes(input.context.patientName.toLowerCase())) {
    score += 3;
  }
  if (hasPatientSpeechSignal(text)) {
    score += 2;
  }
  return score;
}

function collectSpeakerStats(
  segments: AdapterResponse["transcriptSegments"]
): Map<string, { doctorSignals: number; patientSignals: number }> {
  const stats = new Map<string, { doctorSignals: number; patientSignals: number }>();

  for (const segment of segments) {
    const current = stats.get(segment.speakerLabel) ?? {
      doctorSignals: 0,
      patientSignals: 0
    };

    if (hasDoctorSpeechSignal(segment.text)) {
      current.doctorSignals += 1;
    }

    if (hasPatientSpeechSignal(segment.text)) {
      current.patientSignals += 1;
    }

    stats.set(segment.speakerLabel, current);
  }

  return stats;
}

function inferOpenAISpeakerRole(
  segment: AdapterResponse["transcriptSegments"][number],
  input: TranscriptionAdapterInput,
  speakerStats: Map<string, { doctorSignals: number; patientSignals: number }>
): "doctor" | "patient" | "unknown" {
  if (hasMixedTurnSpeechSignal(segment.text)) {
    return "unknown";
  }

  const normalizedLabel = segment.speakerLabel.toLowerCase();
  const doctorName = input.context.doctorName.toLowerCase();
  const patientName = input.context.patientName.toLowerCase();

  if (
    normalizedLabel.includes("doctor") ||
    normalizedLabel.includes("dr.") ||
    normalizedLabel.includes(doctorName)
  ) {
    return "doctor";
  }

  if (
    normalizedLabel.includes("patient") ||
    normalizedLabel.includes(patientName)
  ) {
    return "patient";
  }

  const stats = speakerStats.get(segment.speakerLabel);

  if (
    stats &&
    stats.doctorSignals >= 2 &&
    stats.doctorSignals > stats.patientSignals
  ) {
    return "doctor";
  }

  if (
    stats &&
    stats.patientSignals >= 2 &&
    stats.patientSignals > stats.doctorSignals
  ) {
    return "patient";
  }

  if (hasStrongDoctorSpeechSignal(segment.text)) {
    return "doctor";
  }

  return "unknown";
}

function normalizeIndicScriptTranscriptText(value: string): string {
  if (!/[\u0900-\u097F\u0C00-\u0C7F]/.test(value)) {
    return value;
  }

  // Mark native-script output so downstream review knows this segment still
  // needs human attention; synthesis is instructed to transliterate meaning.
  return value;
}

export function inferSpeakerRole(
  speakerLabel: string,
  text: string,
  input: TranscriptionAdapterInput
): "doctor" | "patient" | "unknown" {
  const normalizedLabel = speakerLabel.toLowerCase();

  if (
    normalizedLabel.includes("doctor") ||
    normalizedLabel.includes("dr.") ||
    normalizedLabel.includes(input.context.doctorName.toLowerCase())
  ) {
    return "doctor";
  }

  if (
    normalizedLabel.includes("patient") ||
    normalizedLabel.includes(input.context.patientName.toLowerCase())
  ) {
    return "patient";
  }

  const normalizedText = text.toLowerCase();

  if (hasStrongDoctorSpeechSignal(normalizedText)) {
    return "doctor";
  }

  return "unknown";
}

function normalizeSpeakerDisplayLabel(
  speakerLabel: string,
  speaker: "doctor" | "patient" | "unknown",
  input: TranscriptionAdapterInput
): string {
  if (speaker === "doctor") {
    return input.context.doctorName.trim();
  }

  if (speaker === "patient") {
    return input.context.patientName.trim();
  }

  if (isGenericSpeakerLabel(speakerLabel)) {
    return "Consultation";
  }

  return speakerLabel;
}

function isGenericSpeakerLabel(value: string): boolean {
  const normalized = value.trim().toLowerCase();

  if (!normalized) {
    return true;
  }

  return (
    /^[a-z]$/.test(normalized) ||
    /^speaker(?:\s+|[-_]?)(\d+|[a-z])?$/.test(normalized) ||
    /^spk(?:\s+|[-_]?)(\d+|[a-z])?$/.test(normalized)
  );
}

function hasStrongDoctorSpeechSignal(value: string): boolean {
  return /\b(take (?:this|one|tablet|capsule|medicine|medication)|continue (?:this|the|your)?\s*(?:medicine|tablet|medication|treatment)|prescrib(?:e|ing)|follow[- ]?up|come back|return for review|review in|blood test|x-ray|xray|scan|mri|ct|ultrasound|sorry to hear|would you describe|can you describe|can you tell me|how long has this been|since when)\b/i.test(
    value
  );
}

function hasDoctorDirectiveSignal(value: string): boolean {
  return /\b(take|continue|prescrib(?:e|ing)|come back|return|review|scan|x-ray|xray|mri|ct|ultrasound)\b/i.test(
    value
  );
}

function hasDoctorSpeechSignal(value: string): boolean {
  return hasStrongDoctorSpeechSignal(value) ||
    /\b(prescribe|diagnosis|symptoms?|fever|cough|pain|dose|dosage|mg|twice daily|once daily|morning|night)\b/i.test(
      value
    );
}

function hasPatientSpeechSignal(value: string): boolean {
  return /\b(i have|i am|i'm|my|mujhe|mere|naku|naaku|pain|fever|cough|vomit|loose motions|headache|since yesterday|for .* days)\b/i.test(
    value
  );
}

function hasStrongPatientSymptomSignal(value: string): boolean {
  return /\b(i have|i feel|it hurts|my chest|my stomach|my head|mujhe|mere|since yesterday|since last night|hard to breathe|shortness of breath)\b/i.test(
    value
  );
}

function hasMixedTurnSpeechSignal(value: string): boolean {
  const normalized = value.toLowerCase();
  const hasDoctorCue = /\b(how are you|what brings you here|i will prescribe|take this|come back|follow[- ]?up)\b/i.test(normalized);
  const hasPatientCue = /\b(i have|it hurts|swelling|i don't know|i am|i'm fine)\b/i.test(normalized);
  const questionCount = (normalized.match(/\?/g) ?? []).length;
  return hasDoctorCue && hasPatientCue && questionCount >= 2;
}


export function normalizeAdapterResponse(
  value: unknown,
  input: TranscriptionAdapterInput
): unknown {
  const source = isRecord(value) ? value : {};
  const metadata = isRecord(source.metadata) ? source.metadata : {};
  const draftDocument = isRecord(source.draftDocument) ? source.draftDocument : {};
  const normalizedDiagnoses = normalizeStringArray(metadata.diagnoses);
  const normalizedNextSteps = normalizeStringArray(draftDocument.nextSteps);
  const normalizedFollowUpPlan = normalizeOptionalString(metadata.followUpPlan);
  const todayIso = getTodayDateInputValue();
  const {
    testsPrescribed: inferredTestsPrescribed,
    remainingNextSteps
  } = inferTestsPrescribed(
    normalizeStringArray(draftDocument.testsPrescribed),
    normalizedNextSteps
  );
  const explicitRevisit = normalizeOptionalString(draftDocument.revisitDate);
  const normalizedExplicitRevisit = normalizeRevisitDateValue(
    explicitRevisit,
    todayIso
  );
  const inferredRevisitDate = normalizedExplicitRevisit ??
    inferRevisitDate(remainingNextSteps, normalizedFollowUpPlan, todayIso);
  const normalizedNextStepsWithFallback =
    !normalizedExplicitRevisit &&
    explicitRevisit &&
    isFollowUpInstruction(explicitRevisit) &&
    !remainingNextSteps.some(
      (step) => step.toLowerCase() === explicitRevisit.toLowerCase()
    )
      ? [...remainingNextSteps, explicitRevisit]
      : remainingNextSteps;

  return {
    summary: normalizeString(source.summary) || buildFallbackSummary(input),
    transcriptSegments: normalizeTranscriptSegments(
      source.transcriptSegments,
      input
    ),
    metadata: {
      ...(normalizeOptionalString(metadata.chiefComplaint)
        ? { chiefComplaint: normalizeOptionalString(metadata.chiefComplaint) }
        : {}),
      symptoms: normalizeStringArray(metadata.symptoms),
      diagnoses: normalizeStringArray(metadata.diagnoses),
      ...(normalizeOptionalString(metadata.followUpPlan)
        ? { followUpPlan: normalizeOptionalString(metadata.followUpPlan) }
        : {}),
      careTeamNotes: normalizeStringArray(metadata.careTeamNotes)
    },
    draftDocument: {
      title:
        normalizeString(draftDocument.title) || "Consultation Draft",
      ...(normalizeOptionalString(draftDocument.chiefComplaint)
        ? {
            chiefComplaint: normalizeOptionalString(
              draftDocument.chiefComplaint
            )
          }
        : normalizeOptionalString(metadata.chiefComplaint)
          ? {
              chiefComplaint: normalizeOptionalString(metadata.chiefComplaint)
            }
          : {}),
      ...(normalizeOptionalString(draftDocument.illnessHistory)
        ? {
            illnessHistory: normalizeOptionalString(
              draftDocument.illnessHistory
            )
          }
        : normalizedDiagnoses.length > 0
          ? {
              illnessHistory: normalizedDiagnoses.join(", ")
            }
          : {}),
      clinicalSummary:
        normalizeString(draftDocument.clinicalSummary) ||
        buildFallbackClinicalSummary(input),
      medications: normalizeMedications(draftDocument.medications),
      testsPrescribed: inferredTestsPrescribed,
      ...(inferredRevisitDate
        ? { revisitDate: inferredRevisitDate }
        : {}),
      nextSteps: normalizedNextStepsWithFallback,
      cautions: normalizeStringArray(draftDocument.cautions)
    }
  };
}

function inferTestsPrescribed(
  explicitTests: string[],
  nextSteps: string[]
): {
  testsPrescribed: string[];
  remainingNextSteps: string[];
} {
  const inferred = new Set(explicitTests);
  const remainingNextSteps: string[] = [];

  for (const step of nextSteps) {
    const normalized = step.trim();

    if (!normalized) {
      continue;
    }

    if (looksLikePrescribedTest(normalized)) {
      inferred.add(normalized);
      continue;
    }

    remainingNextSteps.push(normalized);
  }

  return {
    testsPrescribed: Array.from(inferred),
    remainingNextSteps
  };
}

function inferRevisitDate(
  nextSteps: string[],
  followUpPlan: string | undefined,
  todayIso: string
): string | undefined {
  const candidates = [...nextSteps, ...(followUpPlan ? [followUpPlan] : [])];
  for (const candidate of candidates) {
    const normalized = normalizeOptionalString(candidate);

    if (!normalized) {
      continue;
    }

    if (
      isFollowUpInstruction(normalized) &&
      containsCalendarSignal(normalized)
    ) {
      const parsed = normalizeRevisitDateValue(normalized, todayIso);
      if (parsed) {
        return parsed;
      }
    }
  }

  return undefined;
}

function normalizeRevisitDateValue(
  value: string | undefined,
  todayIso: string
): string | undefined {
  const normalized = normalizeOptionalString(value);
  if (!normalized) {
    return undefined;
  }
  if (/^\d{4}-\d{2}-\d{2}$/.test(normalized)) {
    return normalized;
  }
  const today = parseIsoDate(todayIso);
  if (!today) {
    return undefined;
  }

  const relative = parseRelativeFollowUpDate(normalized, today);
  if (relative) {
    return toIsoDate(relative);
  }
  const calendar = parseCalendarDate(normalized, today);
  if (calendar) {
    return toIsoDate(calendar);
  }
  return undefined;
}

function parseRelativeFollowUpDate(text: string, today: Date): Date | undefined {
  const normalized = text.toLowerCase();
  if (/\btomorrow\b/.test(normalized)) {
    return addDays(today, 1);
  }
  const dayMatch = normalized.match(/\bin\s+(\d+)\s+day(?:s)?\b/);
  if (dayMatch) {
    return addDays(today, Number.parseInt(dayMatch[1] ?? "0", 10));
  }
  const weekMatch = normalized.match(/\bin\s+(\d+)\s+week(?:s)?\b/);
  if (weekMatch) {
    return addDays(today, Number.parseInt(weekMatch[1] ?? "0", 10) * 7);
  }
  return undefined;
}

function parseCalendarDate(text: string, today: Date): Date | undefined {
  const monthPattern = text.match(
    /\b(january|february|march|april|may|june|july|august|september|october|november|december)\s+(\d{1,2})(?:st|nd|rd|th)?(?:,\s*(\d{4}))?\b/i
  );
  if (monthPattern) {
    const monthMap: Record<string, number> = {
      january: 0,
      february: 1,
      march: 2,
      april: 3,
      may: 4,
      june: 5,
      july: 6,
      august: 7,
      september: 8,
      october: 9,
      november: 10,
      december: 11
    };
    const monthKey = (monthPattern[1] ?? "").toLowerCase();
    const month = monthMap[monthKey];
    const day = Number.parseInt(monthPattern[2] ?? "0", 10);
    const year = Number.parseInt(
      monthPattern[3] ?? String(today.getFullYear()),
      10
    );
    if (
      month !== undefined &&
      Number.isInteger(month) &&
      month >= 0 &&
      day >= 1 &&
      day <= 31
    ) {
      return new Date(year, month, day);
    }
  }

  const parsed = new Date(text);
  if (!Number.isNaN(parsed.getTime())) {
    return new Date(parsed.getFullYear(), parsed.getMonth(), parsed.getDate());
  }
  const weekdayMatch = text
    .toLowerCase()
    .match(/\b(?:next\s+)?(monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b/);
  if (!weekdayMatch) {
    return undefined;
  }
  const dayName = weekdayMatch[1];
  const weekdayMap: Record<string, number> = {
    sunday: 0,
    monday: 1,
    tuesday: 2,
    wednesday: 3,
    thursday: 4,
    friday: 5,
    saturday: 6
  };
  const target = weekdayMap[dayName ?? ""] ?? -1;
  if (target < 0) {
    return undefined;
  }
  const current = today.getDay();
  const offset = ((target - current + 7) % 7) || 7;
  return addDays(today, offset);
}

function parseIsoDate(value: string): Date | undefined {
  const match = value.match(/^(\d{4})-(\d{2})-(\d{2})$/);
  if (!match) {
    return undefined;
  }
  const year = Number.parseInt(match[1] ?? "0", 10);
  const month = Number.parseInt(match[2] ?? "0", 10);
  const day = Number.parseInt(match[3] ?? "0", 10);
  if (!year || month < 1 || month > 12 || day < 1 || day > 31) {
    return undefined;
  }
  return new Date(year, month - 1, day);
}

function addDays(date: Date, days: number): Date {
  const next = new Date(date);
  next.setDate(next.getDate() + days);
  return next;
}

function toIsoDate(date: Date): string {
  const year = date.getFullYear();
  const month = String(date.getMonth() + 1).padStart(2, "0");
  const day = String(date.getDate()).padStart(2, "0");
  return `${year}-${month}-${day}`;
}

export function getTodayDateInputValue(now = new Date()): string {
  const year = now.getFullYear();
  const month = String(now.getMonth() + 1).padStart(2, "0");
  const day = String(now.getDate()).padStart(2, "0");
  return `${year}-${month}-${day}`;
}

function looksLikePrescribedTest(value: string): boolean {
  return /\b(x-ray|xray|scan|mri|ct|ultrasound|sonography|cbc|blood test|lab test|investigation|imaging)\b/i.test(
    value
  );
}

function isFollowUpInstruction(value: string): boolean {
  return /\b(follow[- ]?up|revisit|come back|return(?:\s+for\s+review)?|review\s+(?:on|in|after|next|tomorrow|monday|tuesday|wednesday|thursday|friday|saturday|sunday|january|february|march|april|may|june|july|august|september|october|november|december|\d))/i.test(
    value
  );
}

function containsCalendarSignal(value: string): boolean {
  return (
    /\b(mon(day)?|tue(s(day)?)?|wed(nesday)?|thu(r(sday)?)?|fri(day)?|sat(urday)?|sun(day)?)\b/i.test(
      value
    ) ||
    /\b(jan(uary)?|feb(ruary)?|mar(ch)?|apr(il)?|jun(e)?|jul(y)?|aug(ust)?|sep(t|tember)?|oct(ober)?|nov(ember)?|dec(ember)?)\b/i.test(
      value
    ) ||
    /\b(?:\d{1,2}\s+may|may\s+\d{1,2}|may\s+\d{1,2}(st|nd|rd|th)?(?:,\s*\d{4})?)\b/i.test(
      value
    ) ||
    /\b\d{1,2}[/-]\d{1,2}([/-]\d{2,4})?\b/.test(value) ||
    /\b\d{4}\b/.test(value)
  );
}

function normalizeTranscriptSegments(
  value: unknown,
  input: TranscriptionAdapterInput
): Array<{
  speaker: "doctor" | "patient" | "unknown";
  speakerLabel: string;
  text: string;
  startedAtSeconds: number;
  endedAtSeconds: number;
}> {
  if (!Array.isArray(value) || value.length === 0) {
    return [
      {
        speaker: "unknown",
        speakerLabel: "Consultation",
        text:
          "Transcript content was incomplete. Please retry transcription.",
        startedAtSeconds: 0,
        endedAtSeconds: Math.max(1, Math.round(input.audio.durationSeconds || 1))
      }
    ];
  }

  return value
    .map((segment, index) => normalizeTranscriptSegment(segment, index, input))
    .filter((segment): segment is NonNullable<typeof segment> => Boolean(segment));
}

function normalizeTranscriptSegment(
  value: unknown,
  index: number,
  input: TranscriptionAdapterInput
) {
  if (!isRecord(value)) {
    return null;
  }

  const text = normalizeString(value.text);

  if (!text) {
    return null;
  }

  const speaker = normalizeSpeaker(value.speaker);
  const speakerLabel =
    normalizeString(value.speakerLabel) ||
    defaultSpeakerLabel(speaker, input);
  const startedAtSeconds = normalizeNumber(value.startedAtSeconds, index * 30);
  const endedAtSeconds = Math.max(
    startedAtSeconds,
    normalizeNumber(value.endedAtSeconds, startedAtSeconds + 30)
  );

  return {
    speaker,
    speakerLabel,
    text,
    startedAtSeconds,
    endedAtSeconds
  };
}

function normalizeMedications(value: unknown) {
  if (!Array.isArray(value)) {
    return [];
  }

  return value
    .map((item) => {
      if (!isRecord(item)) {
        return null;
      }

      const medicationName = normalizeString(item.medicationName);
      const dosage = normalizeString(item.dosage);
      const instructions = normalizeString(item.instructions);

      if (!medicationName || !dosage || !instructions) {
        return null;
      }

      return {
        medicationName,
        dosage,
        instructions
      };
    })
    .filter((item): item is NonNullable<typeof item> => Boolean(item));
}

function normalizeString(value: unknown): string | undefined {
  if (typeof value !== "string") {
    return undefined;
  }

  const trimmed = value.trim();
  return trimmed.length > 0 ? trimmed : undefined;
}

function normalizeOptionalString(value: unknown): string | undefined {
  if (value === null || value === undefined) {
    return undefined;
  }

  const normalized = normalizeString(value);

  if (!normalized || normalized === "-" || normalized.toLowerCase() === "null") {
    return undefined;
  }

  return normalized;
}

function normalizeStringArray(value: unknown): string[] {
  if (!Array.isArray(value)) {
    return [];
  }

  return value
    .map((item) => normalizeString(item))
    .filter((item): item is string => Boolean(item));
}

function normalizeNumber(value: unknown, fallback: number): number {
  if (typeof value === "number" && Number.isFinite(value) && value >= 0) {
    return value;
  }

  return fallback;
}

function normalizeSpeaker(
  value: unknown
): "doctor" | "patient" | "unknown" {
  if (value === "doctor" || value === "patient" || value === "unknown") {
    return value;
  }

  return "unknown";
}

function defaultSpeakerLabel(
  speaker: "doctor" | "patient" | "unknown",
  input: TranscriptionAdapterInput
): string {
  if (speaker === "doctor") {
    return input.context.doctorName;
  }

  if (speaker === "patient") {
    return input.context.patientName;
  }

  return "Consultation";
}

function buildFallbackSummary(input: TranscriptionAdapterInput): string {
  return `Consultation transcript for ${input.context.patientName} with ${input.context.doctorName}.`;
}

function buildFallbackClinicalSummary(input: TranscriptionAdapterInput): string {
  return `Review the consultation transcript for ${input.context.patientName} and update the clinical summary before generating the final document.`;
}

export async function readProviderErrorBody(response: Response): Promise<string> {
  try {
    const body = await response.text();
    if (body.length <= PROVIDER_ERROR_BODY_MAX_CHARS) {
      return body;
    }

    return `${body.slice(0, PROVIDER_ERROR_BODY_MAX_CHARS)}...[truncated]`;
  } catch (error) {
    return `Unable to read provider error body: ${
      error instanceof Error ? error.message : "Unknown error"
    }`;
  }
}

export function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}
