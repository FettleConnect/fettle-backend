import { randomUUID } from "node:crypto";
import { readFileSync } from "node:fs";

import {
  type ConsultationMetadata,
  type ConsultationSession,
  type DocumentArtifact,
  type DraftConsultationDocument,
  type SessionTranscription,
  type TranscriptSegment,
  draftConsultationDocumentSchema
} from "@medivoice/shared";

export interface DocumentGenerationInput {
  session: ConsultationSession;
  draftDocument: DraftConsultationDocument;
  metadata?: ConsultationMetadata;
}

export interface GeneratedDocumentArtifact {
  artifact: DocumentArtifact;
  html: string;
  pdf: Buffer;
}

export interface TranscriptPdfInput {
  session: ConsultationSession;
  transcription: SessionTranscription;
}

const clinicIdentity = {
  name: "Amor Hospitals",
  phone: "+91 40 6606 9999",
  email: "info@amorhospitals.in",
  address: "Near Balanagar Metro Station 'Y' Junction, Kukatpally"
};

const amorBrand = {
  primaryHex: "#4a419a",
  secondaryHex: "#9b5bb0",
  primaryRgb: [0.29, 0.25, 0.6] as [number, number, number],
  secondaryRgb: [0.61, 0.36, 0.69] as [number, number, number],
  accentRgb: [0.29, 0.41, 0.82] as [number, number, number],
  softFillRgb: [0.97, 0.96, 0.99] as [number, number, number],
  softBorderRgb: [0.88, 0.85, 0.95] as [number, number, number],
  bodyTextRgb: [0.15, 0.14, 0.28] as [number, number, number],
  mutedTextRgb: [0.41, 0.39, 0.54] as [number, number, number]
};

interface EmbeddedJpegAsset {
  name: string;
  width: number;
  height: number;
  data: Buffer;
  mimeType: "image/jpeg";
}

const amorLogoAsset = loadEmbeddedJpegAsset();
const amorLogoDataUri = amorLogoAsset
  ? `data:${amorLogoAsset.mimeType};base64,${amorLogoAsset.data.toString("base64")}`
  : null;

export class DocumentService {
  normalizeDraftDocument(
    input: DraftConsultationDocument
  ): DraftConsultationDocument {
    const parsed = draftConsultationDocumentSchema.parse(input);
    const { revisitDate: parsedRevisitDate, ...draftWithoutRevisitDate } = parsed;
    const revisitDate = normalizeFutureRevisitDate(parsedRevisitDate);

    return {
      ...draftWithoutRevisitDate,
      ...(parsed.chiefComplaint
        ? { chiefComplaint: parsed.chiefComplaint.trim() }
        : {}),
      ...(parsed.illnessHistory
        ? { illnessHistory: parsed.illnessHistory.trim() }
        : {}),
      medications: parsed.medications.filter(
        (item) =>
          item.medicationName.trim() &&
          item.dosage.trim() &&
          item.instructions.trim()
      ),
      testsPrescribed: parsed.testsPrescribed.filter(Boolean),
      ...(revisitDate ? { revisitDate } : {}),
      nextSteps: parsed.nextSteps.filter(Boolean),
      cautions: parsed.cautions.filter(Boolean)
    };
  }

  renderHtml(input: DocumentGenerationInput): string {
    const draft = this.normalizeDraftDocument(input.draftDocument);
    const generatedAt = new Date().toLocaleString();
    const chiefComplaint =
      draft.chiefComplaint ||
      input.metadata?.chiefComplaint ||
      joinOrFallback(input.metadata?.symptoms, "Not recorded");
    const illnessHistoryText =
      draft.illnessHistory ||
      joinOrFallback(input.metadata?.diagnoses, "Not recorded");
    const illnessHistoryLines = formatIllnessHistoryLines(illnessHistoryText);
    const followUp = draft.revisitDate;

    return [
      "<html>",
      "<head>",
      "<style>",
      `body { font-family: Helvetica, Arial, sans-serif; color: #1f2937; margin: 24px; font-size: 12px; background: #ffffff; }`,
      `.sheet { border: 1px solid #ddd8ef; border-radius: 16px; overflow: hidden; }`,
      `.header { background: linear-gradient(135deg, #ffffff 0%, #f7f3ff 100%); color: ${amorBrand.primaryHex}; padding: 22px 24px 20px; border-top: 8px solid ${amorBrand.primaryHex}; border-bottom: 4px solid ${amorBrand.secondaryHex}; }`,
      ".header-top { display: flex; justify-content: space-between; gap: 24px; align-items: flex-start; }",
      ".header-brand { display: flex; flex-direction: column; gap: 12px; flex: 0 0 220px; min-width: 220px; }",
      ".header-brand img { width: 138px; height: auto; display: block; }",
      `.header-title { margin: 18px 0 0; padding-top: 14px; border-top: 1px solid #ddd8ef; font-size: 22px; line-height: 1.2; color: ${amorBrand.primaryHex}; }`,
      ".header-contact { display: flex; flex-direction: column; justify-content: flex-start; flex: 0 0 196px; max-width: 196px; margin-top: 8px; padding-left: 18px; border-left: 3px solid rgba(74, 65, 154, 0.3); }",
      `.header-contact p { margin: 0; color: #4e486d; line-height: 1.3; }`,
      ".header-contact .contact-primary { font-size: 11px; }",
      ".header-contact .contact-secondary { margin-top: 4px; font-size: 11px; }",
      ".header-contact .contact-address { margin-top: 10px; max-width: 150px; font-size: 10px; color: #7a7598; line-height: 1.35; }",
      `.meta { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 12px; padding: 20px 24px; background: #fbfaff; border-bottom: 1px solid #ebe5f7; }`,
      `.meta-item { background: white; border: 1px solid #e5def4; border-radius: 12px; padding: 12px; }`,
      `.label { display: block; font-size: 10px; text-transform: uppercase; letter-spacing: 0.08em; color: #6b6787; margin-bottom: 6px; }`,
      `.value { font-size: 13px; font-weight: 600; color: #262241; }`,
      ".content { padding: 24px; }",
      `.section { margin-top: 18px; border: 1px solid #e7e0f5; border-radius: 12px; padding: 16px; page-break-inside: avoid; }`,
      ".section:first-child { margin-top: 0; }",
      `.section h2 { margin: 0 0 10px; color: ${amorBrand.primaryHex}; font-size: 15px; }`,
      ".section p { margin: 0; line-height: 1.6; }",
      "ul { margin: 0; padding-left: 18px; }",
      "li { margin: 6px 0; }",
      ".medication-table { width: 100%; border-collapse: collapse; font-size: 12px; }",
      `.medication-table th { text-align: left; padding: 10px 12px; background: #f6f2fb; color: ${amorBrand.primaryHex}; font-size: 10px; text-transform: uppercase; letter-spacing: 0.06em; border-bottom: 1px solid #e5def4; }`,
      `.medication-table td { padding: 10px 12px; border-bottom: 1px solid #eee8f8; vertical-align: top; color: #262241; line-height: 1.45; }`,
      ".medication-table tr:last-child td { border-bottom: 0; }",
      ".medication-name { font-weight: 700; }",
      "</style>",
      "</head>",
      "<body>",
      '<div class="sheet">',
      '<div class="header">',
      '<div class="header-top">',
      '<div class="header-brand">',
      amorLogoDataUri
        ? `<img src="${amorLogoDataUri}" alt="${escapeHtml(clinicIdentity.name)} logo" />`
        : `<p style="margin:0;font-size:12px;letter-spacing:0.18em;text-transform:uppercase;color:${amorBrand.primaryHex};">${escapeHtml(clinicIdentity.name)}</p>`,
      "</div>",
      '<div class="header-contact">',
      `<p class="contact-primary">${escapeHtml(clinicIdentity.phone)}</p>`,
      `<p class="contact-secondary">${escapeHtml(clinicIdentity.email)}</p>`,
      `<p class="contact-address">${escapeHtml(clinicIdentity.address)}</p>`,
      "</div>",
      "</div>",
      `<h1 class="header-title">${escapeHtml(draft.title)}</h1>`,
      '<div class="meta">',
      renderMetaItem("Patient Name", input.session.patient.name),
      renderMetaItem("Contact Number", input.session.patient.phoneNumber),
      renderMetaItem("Doctor", input.session.doctor.name),
      renderMetaItem("Generated At", generatedAt),
      renderMetaItem("Chief Complaint", chiefComplaint),
      ...(followUp ? [renderMetaItem("Revisit Date", followUp)] : []),
      "</div>",
      '<div class="content">',
      `<div class="section"><h2>Chief Complaint</h2><p>${escapeHtml(chiefComplaint)}</p></div>`,
      `<div class="section"><h2>Illness History</h2>${renderList(
        illnessHistoryLines
      )}</div>`,
      `<div class="section"><h2>Clinical Summary</h2><p>${escapeHtml(draft.clinicalSummary)}</p></div>`,
      `<div class="section"><h2>Tests Prescribed</h2>${renderList(
        draft.testsPrescribed
      )}</div>`,
      `<div class="section"><h2>Medication Prescribed</h2>${renderMedicationTableHtml(
        draft.medications
      )}</div>`,
      `<div class="section"><h2>Next Steps</h2>${renderList(draft.nextSteps)}</div>`,
      `<div class="section"><h2>Cautions</h2>${renderList(draft.cautions)}</div>`,
      "</div>",
      "</div>",
      "</body>",
      "</html>"
    ].join("");
  }

  generatePdfArtifact(input: DocumentGenerationInput): GeneratedDocumentArtifact {
    const draft = this.normalizeDraftDocument(input.draftDocument);
    const html = this.renderHtml({
      ...input,
      draftDocument: draft
    });
    const pdf = createPrescriptionPdfBuffer({
      session: input.session,
      draftDocument: draft,
      ...(input.metadata ? { metadata: input.metadata } : {})
    });
    const createdAt = new Date().toISOString();
    const fileName = `${slugify(input.session.patient.name)}-${formatFileStamp(createdAt)}.pdf`;

    return {
      artifact: {
        id: randomUUID(),
        fileName,
        mimeType: "application/pdf",
        sizeBytes: pdf.length,
        createdAt,
        delivery: {
          shareTitle: "Consultation Summary",
          shareMessage: `Share ${fileName} with ${input.session.patient.name} after review.`,
          whatsappMessage: buildPatientPdfMessage(input.session.patient.name)
        }
      },
      html,
      pdf
    };
  }

  generateTranscriptPdf(input: TranscriptPdfInput): Buffer {
    const pageWidth = 595;
    const pageHeight = 842;
    const margin = 32;
    const contentWidth = pageWidth - margin * 2;
    const commands: string[] = [];
    const pages: string[] = [];
    let y = pageHeight - margin;
    let pageNumber = 0;

    const summary =
      input.transcription.summary ||
      "Transcript summary was not available for this consultation.";
    const completedAt =
      input.transcription.completedAt || input.session.updatedAt || input.session.createdAt;
    const transcriptSegments =
      input.transcription.transcriptSegments.length > 0
        ? input.transcription.transcriptSegments
        : [
            {
              id: "transcript-fallback",
              speaker: "unknown",
              speakerLabel: "Consultation",
              text: "No transcript segments were available for export.",
              startedAtSeconds: 0,
              endedAtSeconds: 0
            } satisfies TranscriptSegment
          ];

    const drawFilledRect = (
      x: number,
      topY: number,
      width: number,
      height: number,
      fill: [number, number, number]
    ) => {
      commands.push(
        `q ${fill[0]} ${fill[1]} ${fill[2]} rg ${x} ${topY - height} ${width} ${height} re f Q`
      );
    };

    const drawBorderRect = (
      x: number,
      topY: number,
      width: number,
      height: number,
      stroke: [number, number, number]
    ) => {
      commands.push(
        `q 1 w ${stroke[0]} ${stroke[1]} ${stroke[2]} RG ${x} ${topY - height} ${width} ${height} re S Q`
      );
    };

    const drawText = (
      text: string,
      x: number,
      baselineY: number,
      options?: {
        font?: "F1" | "F2";
        fontSize?: number;
        color?: [number, number, number];
      }
    ) => {
      const font = options?.font ?? "F1";
      const fontSize = options?.fontSize ?? 12;
      const color = options?.color ?? [0.12, 0.16, 0.21];
      commands.push(
        `BT ${color[0]} ${color[1]} ${color[2]} rg /${font} ${fontSize} Tf 1 0 0 1 ${x} ${baselineY} Tm (${escapePdfText(
          text
        )}) Tj ET`
      );
    };

    const wrapText = (text: string, width: number, fontSize: number): string[] => {
      const normalized = text.trim();

      if (!normalized) {
        return [""];
      }

      const approxCharsPerLine = Math.max(
        12,
        Math.floor(width / Math.max(5, fontSize * 0.54))
      );
      const words = normalized.split(/\s+/);
      const lines: string[] = [];
      let current = "";

      for (const word of words) {
        const next = current ? `${current} ${word}` : word;
        if (next.length <= approxCharsPerLine) {
          current = next;
        } else {
          if (current) {
            lines.push(current);
          }
          current = word;
        }
      }

      if (current) {
        lines.push(current);
      }

      return lines;
    };

    const drawWrappedTextBlock = (
      text: string,
      x: number,
      topY: number,
      width: number,
      options?: {
        font?: "F1" | "F2";
        fontSize?: number;
        color?: [number, number, number];
        lineHeight?: number;
      }
    ): number => {
      const font = options?.font ?? "F1";
      const fontSize = options?.fontSize ?? 12;
      const lineHeight = options?.lineHeight ?? fontSize + 4;
      const color = options?.color ?? [0.12, 0.16, 0.21];
      const paragraphs = text.split("\n");
      let cursorY = topY;

      for (const paragraph of paragraphs) {
        const lines = wrapText(paragraph, width, fontSize);
        for (const line of lines) {
          drawText(line, x, cursorY - fontSize, { font, fontSize, color });
          cursorY -= lineHeight;
        }
        cursorY -= 4;
      }

      return topY - cursorY;
    };

    const estimateTextHeight = (text: string, width: number, fontSize: number) => {
      const lineHeight = fontSize + 4;
      return (
        text.split("\n").reduce((total, paragraph) => {
          return total + wrapText(paragraph, width, fontSize).length * lineHeight + 4;
        }, 0) || lineHeight
      );
    };

    const finalizePage = () => {
      if (commands.length === 0) {
        return;
      }

      drawText(`Page ${pageNumber}`, pageWidth - margin - 40, margin - 6, {
        font: "F1",
        fontSize: 9,
        color: [0.45, 0.51, 0.58]
      });
      pages.push(commands.join("\n"));
      commands.length = 0;
    };

    const renderHeader = () => {
      pageNumber += 1;
      y = pageHeight - margin;

      const headerHeight = 92;
      drawFilledRect(margin, y, contentWidth, headerHeight, [0.06, 0.36, 0.46]);
      drawText(clinicIdentity.name.toUpperCase(), margin + 18, y - 24, {
        font: "F2",
        fontSize: 11,
        color: [0.82, 0.94, 0.92]
      });
      drawText("Consultation Transcript", margin + 18, y - 50, {
        font: "F2",
        fontSize: 22,
        color: [1, 1, 1]
      });
      drawText(clinicIdentity.phone, margin + 18, y - 68, {
        font: "F1",
        fontSize: 10,
        color: [0.94, 0.98, 0.98]
      });
      drawText(clinicIdentity.email, margin + 160, y - 68, {
        font: "F1",
        fontSize: 10,
        color: [0.94, 0.98, 0.98]
      });
      drawWrappedTextBlock(clinicIdentity.address, margin + 18, y - 78, contentWidth - 36, {
        font: "F1",
        fontSize: 9,
        color: [0.9, 0.97, 0.96],
        lineHeight: 11
      });
      y -= headerHeight + 16;
    };

    const ensureSpace = (heightNeeded: number) => {
      if (y - heightNeeded < margin + 18) {
        finalizePage();
        renderHeader();
      }
    };

    const renderMetaGrid = () => {
      const items = [
        { label: "Patient Name", value: input.session.patient.name },
        { label: "Contact Number", value: input.session.patient.phoneNumber },
        { label: "Doctor", value: input.session.doctor.name },
        { label: "Generated At", value: formatReadableDateTime(completedAt) },
        {
          label: "Transcript Status",
          value:
            input.transcription.requiresRecovery === true ? "Needs review" : "Completed"
        },
        {
          label: "Segments",
          value: String(input.transcription.transcriptSegments.length)
        }
      ];
      const columns = 2;
      const gap = 12;
      const cardWidth = (contentWidth - gap) / columns;
      const cardHeight = 56;
      const rows = Math.ceil(items.length / columns);
      const totalHeight = rows * cardHeight + (rows - 1) * gap;

      ensureSpace(totalHeight + 10);

      items.forEach((item, index) => {
        const row = Math.floor(index / columns);
        const column = index % columns;
        const x = margin + column * (cardWidth + gap);
        const topY = y - row * (cardHeight + gap);

        drawFilledRect(x, topY, cardWidth, cardHeight, [0.97, 0.98, 0.99]);
        drawBorderRect(x, topY, cardWidth, cardHeight, [0.86, 0.9, 0.94]);
        drawText(item.label.toUpperCase(), x + 10, topY - 14, {
          font: "F2",
          fontSize: 8,
          color: [0.39, 0.45, 0.53]
        });
        drawWrappedTextBlock(item.value, x + 10, topY - 25, cardWidth - 20, {
          font: "F2",
          fontSize: 11,
          color: [0.09, 0.16, 0.21],
          lineHeight: 13
        });
      });

      y -= totalHeight + 16;
    };

    const renderSection = (title: string, body: string) => {
      const textHeight = estimateTextHeight(body, contentWidth - 28, 11);
      const sectionHeight = Math.max(70, textHeight + 36);

      ensureSpace(sectionHeight + 12);
      drawFilledRect(margin, y, contentWidth, sectionHeight, [1, 1, 1]);
      drawBorderRect(margin, y, contentWidth, sectionHeight, [0.86, 0.9, 0.94]);
      drawText(title.toUpperCase(), margin + 14, y - 16, {
        font: "F2",
        fontSize: 10,
        color: [0.06, 0.46, 0.43]
      });
      drawWrappedTextBlock(body, margin + 14, y - 31, contentWidth - 28, {
        font: "F1",
        fontSize: 11,
        color: [0.12, 0.16, 0.21],
        lineHeight: 15
      });
      y -= sectionHeight + 12;
    };

    const renderConversationHeading = () => {
      ensureSpace(34);
      drawText("CONVERSATION", margin, y - 10, {
        font: "F2",
        fontSize: 10,
        color: [0.06, 0.46, 0.43]
      });
      y -= 22;
    };

    const renderTranscriptSegment = (segment: TranscriptSegment) => {
      const label = formatTranscriptSpeakerLabel(segment, input.session);
      const timestamp = formatTimestampRange(
        segment.startedAtSeconds,
        segment.endedAtSeconds
      );
      const bubbleWidth = contentWidth - 46;
      const textHeight = estimateTextHeight(segment.text, bubbleWidth - 24, 11);
      const bubbleHeight = Math.max(58, textHeight + 36);
      const leftInset = segment.speaker === "doctor" ? 0 : 26;
      const x = margin + leftInset;
      const fill: [number, number, number] =
        segment.speaker === "doctor" ? [0.93, 0.97, 1] : [0.97, 0.98, 0.99];
      const border: [number, number, number] =
        segment.speaker === "doctor" ? [0.66, 0.8, 0.95] : [0.86, 0.9, 0.94];

      ensureSpace(bubbleHeight + 10);
      drawFilledRect(x, y, bubbleWidth, bubbleHeight, fill);
      drawBorderRect(x, y, bubbleWidth, bubbleHeight, border);
      drawText(label, x + 12, y - 16, {
        font: "F2",
        fontSize: 11,
        color: [0.09, 0.16, 0.21]
      });
      drawText(timestamp, x + bubbleWidth - 64, y - 16, {
        font: "F1",
        fontSize: 8,
        color: [0.45, 0.51, 0.58]
      });
      drawWrappedTextBlock(segment.text, x + 12, y - 30, bubbleWidth - 24, {
        font: "F1",
        fontSize: 11,
        color: [0.12, 0.16, 0.21],
        lineHeight: 15
      });
      y -= bubbleHeight + 10;
    };

    renderHeader();
    renderMetaGrid();
    renderSection("Clinical Summary", summary);
    renderConversationHeading();
    transcriptSegments.forEach(renderTranscriptSegment);
    finalizePage();

    return buildPdfFromPages(pages, pageWidth, pageHeight);
  }
}

function formatFileStamp(value: string): string {
  return value.replace(/[-:]/g, "").replace(/\.\d{3}Z$/, "Z");
}

function buildPatientPdfMessage(patientName: string): string {
  return `Hello ${patientName}, your digital prescription is attached. Reply if you need help.`;
}

function renderMetaItem(label: string, value: string): string {
  return `<div class="meta-item"><span class="label">${escapeHtml(label)}</span><span class="value">${escapeHtml(value || "Not recorded")}</span></div>`;
}

function joinOrFallback(items: string[] | undefined, fallback: string): string {
  if (!items || items.length === 0) {
    return fallback;
  }

  return items.join(", ");
}

function renderList(items: string[]): string {
  if (items.length === 0) {
    return "<p>Not available.</p>";
  }

  return `<ul>${items.map((item) => `<li>${escapeHtml(item)}</li>`).join("")}</ul>`;
}

function formatIllnessHistoryLines(value: string): string[] {
  const cleaned = value.trim();

  if (!cleaned || cleaned === "Not recorded") {
    return ["Not recorded"];
  }

  const explicitLines = cleaned
    .split(/\r?\n|;/)
    .map((item) => item.replace(/^[-*•]\s*/, "").trim())
    .filter(Boolean);

  if (explicitLines.length > 1) {
    return explicitLines.slice(0, 5).map(shortenClinicalPoint);
  }

  return cleaned
    .split(/(?<=[.!?])\s+/)
    .map((item) => item.replace(/[.!?]+$/, "").trim())
    .filter(Boolean)
    .slice(0, 5)
    .map(shortenClinicalPoint);
}

function shortenClinicalPoint(value: string): string {
  const words = value.split(/\s+/);

  if (words.length <= 18) {
    return value;
  }

  return `${words.slice(0, 18).join(" ")}...`;
}

function normalizeFutureRevisitDate(value: string | undefined): string | undefined {
  const trimmed = value?.trim();

  if (!trimmed) {
    return undefined;
  }

  if (!/^\d{4}-\d{2}-\d{2}$/.test(trimmed)) {
    return trimmed;
  }

  return trimmed >= getTomorrowDateInputValue() ? trimmed : undefined;
}

function getTomorrowDateInputValue(now = new Date()): string {
  const tomorrow = new Date(now);
  tomorrow.setHours(0, 0, 0, 0);
  tomorrow.setDate(tomorrow.getDate() + 1);

  const year = tomorrow.getFullYear();
  const month = String(tomorrow.getMonth() + 1).padStart(2, "0");
  const day = String(tomorrow.getDate()).padStart(2, "0");

  return `${year}-${month}-${day}`;
}

function renderMedicationTableHtml(
  items: DraftConsultationDocument["medications"]
): string {
  const rows =
    items.length > 0
      ? items
      : [
          {
            medicationName: "No medications prescribed",
            dosage: "-",
            instructions: "-"
          }
        ];

  return [
    '<table class="medication-table">',
    "<thead>",
    "<tr>",
    "<th>Medicine</th>",
    "<th>Dose</th>",
    "<th>Instructions</th>",
    "</tr>",
    "</thead>",
    "<tbody>",
    ...rows.map(
      (item) =>
        `<tr><td class="medication-name">${escapeHtml(item.medicationName)}</td><td>${escapeHtml(item.dosage)}</td><td>${escapeHtml(item.instructions)}</td></tr>`
    ),
    "</tbody>",
    "</table>"
  ].join("");
}

function renderPlainList(items: string[], fallback: string): string[] {
  if (items.length === 0) {
    return [fallback];
  }

  return items.map((item) => `- ${item}`);
}

function escapeHtml(value: string): string {
  return value
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;");
}

function formatReadableDateTime(value: string): string {
  const parsed = new Date(value);

  if (Number.isNaN(parsed.getTime())) {
    return value;
  }

  return parsed.toLocaleString("en-IN", {
    dateStyle: "medium",
    timeStyle: "short"
  });
}

function formatTimestampRange(startedAtSeconds: number, endedAtSeconds: number): string {
  return `${formatMinutesAndSeconds(startedAtSeconds)}-${formatMinutesAndSeconds(endedAtSeconds)}`;
}

function formatMinutesAndSeconds(totalSeconds: number): string {
  const rounded = Math.max(0, Math.floor(totalSeconds));
  const minutes = Math.floor(rounded / 60);
  const seconds = rounded % 60;

  return `${minutes}:${String(seconds).padStart(2, "0")}`;
}

function formatTranscriptSpeakerLabel(
  segment: TranscriptSegment,
  session: ConsultationSession
): string {
  if (segment.speaker === "doctor") {
    return session.doctor.name.startsWith("Dr.")
      ? session.doctor.name
      : `Dr. ${session.doctor.name}`;
  }

  if (segment.speaker === "patient") {
    return session.patient.name;
  }

  return segment.speakerLabel || "Consultation";
}

function slugify(value: string): string {
  return value.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "");
}

function createPrescriptionPdfBuffer(input: DocumentGenerationInput): Buffer {
  const pageWidth = 595;
  const pageHeight = 842;
  const margin = 32;
  const contentWidth = pageWidth - margin * 2;
  const commands: string[] = [];
  const pages: string[] = [];
  let y = pageHeight - margin;

  const chiefComplaint =
    input.draftDocument.chiefComplaint ||
    input.metadata?.chiefComplaint ||
    joinOrFallback(input.metadata?.symptoms, "Not recorded");
  const illnessHistory = joinOrFallback(
    input.draftDocument.illnessHistory
      ? [input.draftDocument.illnessHistory]
      : input.metadata?.diagnoses,
    "Not recorded"
  );
  const illnessHistoryLines = formatIllnessHistoryLines(illnessHistory);
  const followUp = input.draftDocument.revisitDate;
  const medicationItems = input.draftDocument.medications;
  const testsLines =
    input.draftDocument.testsPrescribed.length > 0
      ? input.draftDocument.testsPrescribed
      : ["No tests prescribed"];
  const nextStepLines =
    input.draftDocument.nextSteps.length > 0
      ? input.draftDocument.nextSteps
      : ["No next steps recorded"];
  const cautionLines =
    input.draftDocument.cautions.length > 0
      ? input.draftDocument.cautions
      : ["No cautions recorded"];

  const newPage = () => {
    if (commands.length > 0) {
      pages.push(commands.join("\n"));
      commands.length = 0;
    }
    y = pageHeight - margin;
  };

  const ensureSpace = (heightNeeded: number) => {
    if (y - heightNeeded < margin) {
      newPage();
    }
  };

  const drawFilledRect = (
    x: number,
    topY: number,
    width: number,
    height: number,
    fill: [number, number, number]
  ) => {
    commands.push(
      `q ${fill[0]} ${fill[1]} ${fill[2]} rg ${x} ${topY - height} ${width} ${height} re f Q`
    );
  };

  const drawBorderRect = (
    x: number,
    topY: number,
    width: number,
    height: number,
    stroke: [number, number, number]
  ) => {
    commands.push(
      `q 1 w ${stroke[0]} ${stroke[1]} ${stroke[2]} RG ${x} ${topY - height} ${width} ${height} re S Q`
    );
  };

  const drawText = (
    text: string,
    x: number,
    baselineY: number,
    options?: {
      font?: "F1" | "F2";
      fontSize?: number;
      color?: [number, number, number];
    }
  ) => {
    const font = options?.font ?? "F1";
    const fontSize = options?.fontSize ?? 12;
    const color = options?.color ?? [0.12, 0.16, 0.21];
    commands.push(
      `BT ${color[0]} ${color[1]} ${color[2]} rg /${font} ${fontSize} Tf 1 0 0 1 ${x} ${baselineY} Tm (${escapePdfText(
        text
      )}) Tj ET`
    );
  };

  const drawJpegImage = (
    name: string,
    x: number,
    topY: number,
    width: number,
    height: number
  ) => {
    commands.push(`q ${width} 0 0 ${height} ${x} ${topY - height} cm /${name} Do Q`);
  };

  const wrapText = (text: string, width: number, fontSize: number): string[] => {
    const normalized = text.trim();

    if (!normalized) {
      return [""];
    }

    const approxCharsPerLine = Math.max(
      12,
      Math.floor(width / Math.max(5, fontSize * 0.54))
    );
    const splitLongToken = (value: string): string[] => {
      if (value.length <= approxCharsPerLine) {
        return [value];
      }

      const chunks: string[] = [];
      for (let index = 0; index < value.length; index += approxCharsPerLine) {
        chunks.push(value.slice(index, index + approxCharsPerLine));
      }
      return chunks;
    };
    const words = normalized.split(/\s+/);
    const lines: string[] = [];
    let current = "";

    for (const rawWord of words) {
      const wordParts = splitLongToken(rawWord);

      for (const word of wordParts) {
        const next = current ? `${current} ${word}` : word;
        if (next.length <= approxCharsPerLine) {
          current = next;
        } else {
          if (current) {
            lines.push(current);
          }
          current = word;
        }
      }
    }

    if (current) {
      lines.push(current);
    }

    return lines;
  };

  const drawWrappedTextBlock = (
    text: string,
    x: number,
    topY: number,
    width: number,
    options?: {
      font?: "F1" | "F2";
      fontSize?: number;
      color?: [number, number, number];
      lineHeight?: number;
    }
  ): number => {
    const font = options?.font ?? "F1";
    const fontSize = options?.fontSize ?? 12;
    const lineHeight = options?.lineHeight ?? fontSize + 4;
    const color = options?.color ?? [0.12, 0.16, 0.21];
    const paragraphs = text.split("\n");
    let cursorY = topY;

    for (const paragraph of paragraphs) {
      const lines = wrapText(paragraph, width, fontSize);
      for (const line of lines) {
        drawText(line, x, cursorY - fontSize, { font, fontSize, color });
        cursorY -= lineHeight;
      }
      cursorY -= 4;
    }

    return topY - cursorY;
  };

  const estimateTextHeight = (text: string, width: number, fontSize: number) => {
    const lineHeight = fontSize + 4;
    return (
      text.split("\n").reduce((total, paragraph) => {
        return total + wrapText(paragraph, width, fontSize).length * lineHeight + 4;
      }, 0) || lineHeight
    );
  };

  const renderMetaGrid = () => {
    const items = [
      { label: "Patient Name", value: input.session.patient.name },
      { label: "Contact Number", value: input.session.patient.phoneNumber },
      { label: "Doctor", value: input.session.doctor.name },
      { label: "Date and Time", value: new Date().toLocaleString("en-IN") },
      { label: "Chief Complaint", value: chiefComplaint }
    ];

    if (followUp) {
      items.push({ label: "Revisit Date", value: followUp });
    }
    const columns = 2;
    const gap = 12;
    const cardWidth = (contentWidth - gap) / columns;
    const cardHeights = items.map((item) =>
      Math.max(52, 24 + estimateTextHeight(item.value, cardWidth - 20, 11) + 8)
    );
    const rows = Math.ceil(items.length / columns);
    const rowHeights = Array.from({ length: rows }, (_, rowIndex) => {
      const first = cardHeights[rowIndex * columns] ?? 52;
      const second = cardHeights[rowIndex * columns + 1] ?? 52;
      return Math.max(first, second);
    });
    const totalHeight =
      rowHeights.reduce((sum, value) => sum + value, 0) + (rows - 1) * gap;

    ensureSpace(totalHeight + 10);

    let rowTopY = y;
    for (let row = 0; row < rows; row += 1) {
      const rowHeight = rowHeights[row] ?? 52;

      for (let column = 0; column < columns; column += 1) {
        const item = items[row * columns + column];
        if (!item) {
          continue;
        }

        const x = margin + column * (cardWidth + gap);
        drawFilledRect(x, rowTopY, cardWidth, rowHeight, amorBrand.softFillRgb);
        drawBorderRect(x, rowTopY, cardWidth, rowHeight, amorBrand.softBorderRgb);
        drawText(item.label.toUpperCase(), x + 10, rowTopY - 14, {
          font: "F2",
          fontSize: 8,
          color: amorBrand.mutedTextRgb
        });
        drawWrappedTextBlock(item.value, x + 10, rowTopY - 24, cardWidth - 20, {
          font: "F2",
          fontSize: 11,
          color: amorBrand.bodyTextRgb,
          lineHeight: 13
        });
      }

      rowTopY -= rowHeight + gap;
    }

    y -= totalHeight + 18;
  };

  const renderSection = (
    title: string,
    bodyLines: string[],
    variant: "text" | "list" = "text"
  ) => {
    const contentText =
      variant === "list"
        ? bodyLines.map((line) => `- ${line}`).join("\n")
        : bodyLines.join("\n\n");
    const textHeight = estimateTextHeight(contentText, contentWidth - 28, 11);
    const sectionHeight = Math.max(66, textHeight + 34);

    ensureSpace(sectionHeight + 12);
    drawFilledRect(margin, y, contentWidth, sectionHeight, [1, 1, 1]);
    drawBorderRect(margin, y, contentWidth, sectionHeight, amorBrand.softBorderRgb);
    drawText(title.toUpperCase(), margin + 14, y - 16, {
      font: "F2",
      fontSize: 10,
      color: amorBrand.primaryRgb
    });
    drawWrappedTextBlock(contentText, margin + 14, y - 30, contentWidth - 28, {
      font: "F1",
      fontSize: 11,
      color: amorBrand.bodyTextRgb,
      lineHeight: 15
    });
    y -= sectionHeight + 12;
  };

  const renderMedicationTable = () => {
    ensureSpace(92);

    const tableX = margin;
    const tableWidth = contentWidth;
    const innerTableWidth = tableWidth - 28;
    const colWidths: [number, number, number] = [
      162,
      96,
      innerTableWidth - 162 - 96
    ];
    const headerHeight = 28;
    const cellPaddingX = 10;
    const cellPaddingTop = 9;
    const bodyFontSize = 10;
    const bodyLineHeight = 13;
    const rows =
      medicationItems.length > 0
        ? medicationItems
        : [
            {
              medicationName: "No medications prescribed",
              dosage: "-",
              instructions: "-"
            }
          ];
    const headers: [string, string, string] = ["MEDICINE", "DOSE", "INSTRUCTIONS"];

    const rowHeights = rows.map((item) => {
      const medicineHeight = estimateTextHeight(
        item.medicationName,
        colWidths[0] - cellPaddingX * 2,
        bodyFontSize
      );
      const dosageHeight = estimateTextHeight(
        item.dosage,
        colWidths[1] - cellPaddingX * 2,
        bodyFontSize
      );
      const instructionsHeight = estimateTextHeight(
        item.instructions,
        colWidths[2] - cellPaddingX * 2,
        bodyFontSize
      );

      return Math.max(
        24,
        medicineHeight + cellPaddingTop,
        dosageHeight + cellPaddingTop,
        instructionsHeight + cellPaddingTop
      );
    });

    let rowIndex = 0;

    while (rowIndex < rows.length) {
      const firstRowHeight = rowHeights[rowIndex] ?? 24;
      ensureSpace(18 + headerHeight + firstRowHeight + 16 + 12);

      const availableRowsHeight = y - (margin + 18) - 30 - headerHeight;
      if (availableRowsHeight <= 0) {
        newPage();
        continue;
      }

      let chunkRowsHeight = 0;
      let chunkRowCount = 0;
      while (rowIndex + chunkRowCount < rows.length) {
        const nextRowHeight = rowHeights[rowIndex + chunkRowCount] ?? 24;
        if (
          chunkRowsHeight + nextRowHeight > availableRowsHeight &&
          chunkRowCount > 0
        ) {
          break;
        }
        if (nextRowHeight > availableRowsHeight && chunkRowCount === 0) {
          chunkRowsHeight = availableRowsHeight;
          chunkRowCount = 1;
          break;
        }
        chunkRowsHeight += nextRowHeight;
        chunkRowCount += 1;
      }

      if (chunkRowCount === 0) {
        newPage();
        continue;
      }

      const sectionHeight = 18 + headerHeight + chunkRowsHeight + 16;
      drawFilledRect(tableX, y, tableWidth, sectionHeight, [1, 1, 1]);
      drawBorderRect(tableX, y, tableWidth, sectionHeight, amorBrand.softBorderRgb);
      drawText("MEDICATION PRESCRIBED", tableX + 14, y - 16, {
        font: "F2",
        fontSize: 10,
        color: amorBrand.primaryRgb
      });

      let tableTopY = y - 30;
      drawFilledRect(
        tableX + 14,
        tableTopY,
        innerTableWidth,
        headerHeight,
        amorBrand.softFillRgb
      );
      drawBorderRect(
        tableX + 14,
        tableTopY,
        innerTableWidth,
        headerHeight,
        amorBrand.softBorderRgb
      );

      let currentX = tableX + 14;
      for (let index = 0 as 0 | 1 | 2; index < headers.length; index += 1) {
        const header = headers[index];
        drawText(header, currentX + cellPaddingX, tableTopY - 11, {
          font: "F2",
          fontSize: 9,
          color: amorBrand.primaryRgb
        });

        if (index < headers.length - 1) {
          currentX += colWidths[index];
          drawFilledRect(currentX, tableTopY, 1, headerHeight, amorBrand.softBorderRgb);
        }
      }

      tableTopY -= headerHeight;

      for (let offset = 0; offset < chunkRowCount; offset += 1) {
        const absoluteRowIndex = rowIndex + offset;
        const item = rows[absoluteRowIndex];
        if (!item) {
          continue;
        }
        const rowHeight = rowHeights[absoluteRowIndex] ?? 24;
        const rowTopY = tableTopY;
        const rowBottomY = rowTopY - rowHeight;

        if (offset > 0) {
          drawFilledRect(tableX + 14, rowTopY, innerTableWidth, 1, amorBrand.softBorderRgb);
        }

        const columnValues: [string, string, string] = [
          item.medicationName,
          item.dosage,
          item.instructions
        ];
        let columnX = tableX + 14;

        for (
          let columnIndex = 0 as 0 | 1 | 2;
          columnIndex < columnValues.length;
          columnIndex += 1
        ) {
          const value = columnValues[columnIndex];
          drawWrappedTextBlock(
            value,
            columnX + cellPaddingX,
            rowTopY - cellPaddingTop,
            colWidths[columnIndex] - cellPaddingX * 2,
            {
              font: columnIndex === 0 ? "F2" : "F1",
              fontSize: bodyFontSize,
              color: amorBrand.bodyTextRgb,
              lineHeight: bodyLineHeight
            }
          );

          if (columnIndex < columnValues.length - 1) {
            columnX += colWidths[columnIndex];
            drawFilledRect(columnX, rowTopY, 1, rowHeight, amorBrand.softBorderRgb);
          }
        }

        tableTopY = rowBottomY;
      }

      rowIndex += chunkRowCount;
      y -= sectionHeight + 12;

      if (rowIndex < rows.length) {
        ensureSpace(18 + headerHeight + (rowHeights[rowIndex] ?? 24) + 16 + 12);
      }
    }
  };

  const contactDividerX = margin + contentWidth - 158;
  const titleRuleX = margin + 18;
  const titleRuleWidth = contactDividerX - titleRuleX - 18;
  const titleHeight = estimateTextHeight(input.draftDocument.title, titleRuleWidth, 20);
  const headerHeight = Math.max(124, 98 + titleHeight);
  const topBandHeight = 10;
  ensureSpace(headerHeight + 30);
  drawFilledRect(margin, y, contentWidth, topBandHeight, amorBrand.primaryRgb);
  drawFilledRect(
    margin + contentWidth * 0.58,
    y,
    contentWidth * 0.42,
    topBandHeight,
    amorBrand.secondaryRgb
  );
  y -= topBandHeight;

  ensureSpace(headerHeight + 20);
  drawFilledRect(margin, y, contentWidth, headerHeight, [1, 1, 1]);
  drawBorderRect(margin, y, contentWidth, headerHeight, amorBrand.softBorderRgb);

  const logoTopY = y - 18;
  if (amorLogoAsset) {
    drawJpegImage(amorLogoAsset.name, margin + 18, logoTopY, 112, 53);
  } else {
    drawText(clinicIdentity.name.toUpperCase(), margin + 20, y - 30, {
      font: "F2",
      fontSize: 18,
      color: amorBrand.primaryRgb
    });
  }

  const contactTextX = contactDividerX + 16;
  drawFilledRect(contactDividerX, y - 26, 2, 64, amorBrand.primaryRgb);
  drawText(clinicIdentity.phone, contactTextX, y - 20, {
    font: "F1",
    fontSize: 10,
    color: amorBrand.bodyTextRgb
  });
  drawText(clinicIdentity.email, contactTextX, y - 36, {
    font: "F1",
    fontSize: 10,
    color: amorBrand.bodyTextRgb
  });
  drawWrappedTextBlock(clinicIdentity.address, contactTextX, y - 52, 134, {
    font: "F1",
    fontSize: 8.5,
    color: amorBrand.mutedTextRgb,
    lineHeight: 10.5
  });

  const titleRuleY = y - 74;
  drawFilledRect(titleRuleX, titleRuleY, titleRuleWidth, 1, amorBrand.softBorderRgb);
  drawWrappedTextBlock(input.draftDocument.title, margin + 20, y - 90, titleRuleWidth, {
    font: "F2",
    fontSize: 20,
    color: amorBrand.primaryRgb,
    lineHeight: 24
  });
  y -= headerHeight + 12;
  drawFilledRect(margin, y, contentWidth, 4, amorBrand.secondaryRgb);
  y -= 16;

  renderMetaGrid();
  renderSection("Chief Complaint", [chiefComplaint]);
  renderSection("Illness History", illnessHistoryLines, "list");
  renderSection("Clinical Summary", [input.draftDocument.clinicalSummary]);
  renderSection("Tests Prescribed", testsLines, "list");
  renderMedicationTable();
  renderSection("Next Steps", nextStepLines, "list");
  renderSection("Cautions", cautionLines, "list");

  if (commands.length > 0) {
    pages.push(commands.join("\n"));
  }

  return buildPdfFromPages(pages, pageWidth, pageHeight, amorLogoAsset ? [amorLogoAsset] : []);
}

function buildPdfFromPages(
  pageContents: string[],
  pageWidth: number,
  pageHeight: number,
  embeddedImages: EmbeddedJpegAsset[] = []
): Buffer {
  const totalPageObjects = pageContents.length * 2;
  const fontObjectStart = 3 + totalPageObjects;
  const imageObjectStart = fontObjectStart + 2;
  const objectBuffers: Buffer[] = new Array(
    2 + totalPageObjects + 2 + embeddedImages.length
  );

  objectBuffers[0] = Buffer.from("<< /Type /Catalog /Pages 2 0 R >>", "utf8");

  const pageObjectNumbers = pageContents.map((_, index) => 3 + index * 2);
  objectBuffers[1] = Buffer.from(
    `<< /Type /Pages /Count ${pageContents.length} /Kids [${pageObjectNumbers
      .map((id) => `${id} 0 R`)
      .join(" ")}] >>`,
    "utf8"
  );
  const xObjectDictionary =
    embeddedImages.length > 0
      ? ` /XObject << ${embeddedImages
          .map((image, index) => `/${image.name} ${imageObjectStart + index} 0 R`)
          .join(" ")} >>`
      : "";

  for (let index = 0; index < pageContents.length; index += 1) {
    const pageObjectId = 3 + index * 2;
    const contentObjectId = pageObjectId + 1;
    const content = pageContents[index] ?? "";

    objectBuffers[pageObjectId - 1] = Buffer.from(
      `<< /Type /Page /Parent 2 0 R /MediaBox [0 0 ${pageWidth} ${pageHeight}] /Contents ${contentObjectId} 0 R /Resources << /Font << /F1 ${fontObjectStart} 0 R /F2 ${fontObjectStart + 1} 0 R >>${xObjectDictionary} >> >>`,
      "utf8"
    );
    objectBuffers[contentObjectId - 1] = Buffer.from(
      `<< /Length ${Buffer.byteLength(content, "utf8")} >>\nstream\n${content}\nendstream`,
      "utf8"
    );
  }

  objectBuffers[fontObjectStart - 1] = Buffer.from(
    "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    "utf8"
  );
  objectBuffers[fontObjectStart] = Buffer.from(
    "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold >>",
    "utf8"
  );

  embeddedImages.forEach((image, index) => {
    objectBuffers[imageObjectStart + index - 1] = Buffer.concat([
      Buffer.from(
        `<< /Type /XObject /Subtype /Image /Width ${image.width} /Height ${image.height} /ColorSpace /DeviceRGB /BitsPerComponent 8 /Filter /DCTDecode /Length ${image.data.length} >>\nstream\n`,
        "utf8"
      ),
      image.data,
      Buffer.from("\nendstream", "utf8")
    ]);
  });

  const pdfHeader = Buffer.from("%PDF-1.4\n", "utf8");
  const parts: Buffer[] = [pdfHeader];
  const offsets = [0];
  let currentOffset = pdfHeader.length;

  objectBuffers.forEach((objectBuffer, index) => {
    const objectRecord = Buffer.concat([
      Buffer.from(`${index + 1} 0 obj\n`, "utf8"),
      objectBuffer,
      Buffer.from("\nendobj\n", "utf8")
    ]);
    offsets.push(currentOffset);
    parts.push(objectRecord);
    currentOffset += objectRecord.length;
  });

  const xrefOffset = currentOffset;
  const xref = [
    `xref`,
    `0 ${objectBuffers.length + 1}`,
    "0000000000 65535 f ",
    ...offsets.slice(1).map((offset) => `${offset.toString().padStart(10, "0")} 00000 n `),
    `trailer`,
    `<< /Size ${objectBuffers.length + 1} /Root 1 0 R >>`,
    `startxref`,
    `${xrefOffset}`,
    "%%EOF"
  ].join("\n");

  parts.push(Buffer.from(xref, "utf8"));
  return Buffer.concat(parts);
}

function escapePdfText(value: string): string {
  return value
    .replaceAll("\\", "\\\\")
    .replaceAll("(", "\\(")
    .replaceAll(")", "\\)");
}

function readJpegDimensions(data: Buffer): { width: number; height: number } {
  if (data.length < 4 || data[0] !== 0xff || data[1] !== 0xd8) {
    throw new Error("Invalid JPEG data");
  }

  let offset = 2;
  while (offset + 9 < data.length) {
    while (offset < data.length && data[offset] === 0xff) {
      offset += 1;
    }

    const marker = data[offset];
    offset += 1;

    if (marker === undefined) {
      break;
    }

    if (marker === 0xd9 || marker === 0xda) {
      break;
    }

    if (offset + 1 >= data.length) {
      break;
    }

    const segmentLength = data.readUInt16BE(offset);
    if (segmentLength < 2 || offset + segmentLength > data.length) {
      break;
    }

    const isStartOfFrame =
      marker >= 0xc0 &&
      marker <= 0xcf &&
      ![0xc4, 0xc8, 0xcc].includes(marker);

    if (isStartOfFrame) {
      if (segmentLength < 7) {
        break;
      }

      return {
        height: data.readUInt16BE(offset + 3),
        width: data.readUInt16BE(offset + 5)
      };
    }

    offset += segmentLength;
  }

  throw new Error("Unable to determine JPEG dimensions");
}

function loadEmbeddedJpegAsset(): EmbeddedJpegAsset | null {
  try {
    const data = readFileSync(new URL("../assets/amor-logo.jpg", import.meta.url));
    const { width, height } = readJpegDimensions(data);
    return {
      name: "AmorLogo",
      width,
      height,
      data,
      mimeType: "image/jpeg"
    };
  } catch {
    return null;
  }
}
