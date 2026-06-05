import { randomUUID } from "node:crypto";

import {
  type DraftConsultationDocument,
  type ReportContextChunk,
  draftConsultationDocumentSchema,
  reportContextChunkSchema
} from "@medivoice/shared";
import { PDFDocument } from "pdf-lib";
import { z } from "zod";

import { AIProviderError } from "./errors.js";
import { emitKpi } from "./kpi.js";
import { runWithReliability } from "./reliability.js";
import { type StoredReportContextUpload } from "./session-repository.js";

const reportContextPrompt = `
You extract useful clinical context from physical reports uploaded during a consultation.
Return JSON only with this exact shape:
{
  "chunks": [
    {
      "title": string,
      "category": string,
      "sourceFileName": string,
      "text": string
    }
  ]
}
Create concise chunks that a doctor can choose from. Prefer clinically meaningful groups such as diagnosis, history, lab abnormalities, imaging findings, medications, allergy, cautions, follow-up, or prior procedure.
Set sourceFileName to the exact uploaded file name that supplied the chunk.
Do not invent facts. Ignore billing details, page headers, footers, and duplicate boilerplate.
Use clear clinical English.
`.trim();

const reportContextRepairPrompt = `
Return strict JSON only. Do not include markdown fences, prose, or comments.
The payload must be exactly:
{
  "chunks": [
    {
      "title": string,
      "category": string,
      "sourceFileName": string,
      "text": string
    }
  ]
}
Use only sourceFileName values from the uploaded file names provided in the request.
If unsure, still return best-effort chunks with non-empty title/category/sourceFileName/text.
`.trim();

const draftSuggestionPrompt = `
You update an existing consultation draft using selected report context.
Return JSON only with this exact shape:
{
  "draftDocument": {
    "title": string,
    "chiefComplaint": string,
    "illnessHistory": string,
    "clinicalSummary": string,
    "medications": [
      {
        "medicationName": string,
        "dosage": string,
        "instructions": string
      }
    ],
    "testsPrescribed": string[],
    "revisitDate": string,
    "nextSteps": string[],
    "cautions": string[]
  },
  "changedFields": string[]
}
Preserve the existing draft unless the report context clearly improves it.
Merge report context into existing fields only. Do not add a separate report section.
If the report mentions prior diagnoses, investigations, surgeries, or longitudinal findings, update illnessHistory or clinicalSummary.
If the report suggests risk, allergy, abnormal findings, or precautions, update cautions.
If report findings imply follow-up but no exact date, use nextSteps, not revisitDate.
Omit optional string fields when no meaningful value is available. Use empty arrays for missing list values.
`.trim();

const draftSuggestionRepairPrompt = `
Return strict JSON only. Do not include markdown fences, prose, or comments.
The payload must be exactly:
{
  "draftDocument": {
    "title": string,
    "chiefComplaint": string,
    "illnessHistory": string,
    "clinicalSummary": string,
    "medications": [
      {
        "medicationName": string,
        "dosage": string,
        "instructions": string
      }
    ],
    "testsPrescribed": string[],
    "revisitDate": string,
    "nextSteps": string[],
    "cautions": string[]
  },
  "changedFields": string[]
}
changedFields must contain only keys from draftDocument that changed.
`.trim();

const extractedChunksSchema = z.object({
  chunks: z.array(
    z.object({
      title: z.string().trim().min(1).max(120),
      category: z.string().trim().min(1).max(80),
      sourceFileName: z.string().trim().min(1).max(255),
      sourceIndex: z.number().int().min(0).max(99).optional(),
      pageNumber: z.number().int().min(1).max(9999).optional(),
      text: z.string().trim().min(1).max(4000)
    })
  ).max(20)
});

const draftSuggestionSchema = z.object({
  draftDocument: draftConsultationDocumentSchema,
  changedFields: z.array(z.string().trim().min(1))
});

type ReportContextFailureCode =
  | "provider_timeout"
  | "provider_http_error"
  | "provider_schema_mismatch"
  | "source_file_mapping_failed"
  | "no_extractable_content"
  | "provider_transport_error"
  | "too_many_pages";

interface TooManyPagesDetail {
  requestedUnits: number;
  maxUnits: number;
}

interface ReportContextExtractionUnit {
  readonly upload: StoredReportContextUpload;
  readonly pageNumber?: number;
  readonly unitIndex: number;
  readonly unitCount: number;
}

interface ReportContextExtractInput {
  sessionId: string;
  uploads: StoredReportContextUpload[];
  onUnitProgress?: (progress: {
    totalUnits: number;
    completedUnits: number;
    failedUnits: number;
    elapsedMs: number;
    extractedChunks: ReportContextChunk[];
  }) => Promise<void> | void;
}

async function runSharedReportContextExtraction(
  input: ReportContextExtractInput,
  options: {
    providerName: string;
    maxUnits: number;
    concurrency: number;
    extractUnit: (unit: ReportContextExtractionUnit) => Promise<ReportContextChunk[]>;
  }
): Promise<ReportContextChunk[]> {
  const startedAt = Date.now();
  let units: ReportContextExtractionUnit[];
  try {
    units = await expandReportContextUploads(input.uploads, options.maxUnits);
  } catch (error) {
    emitKpi(
      "report_context.preprocess",
      "failure",
      {
        provider: options.providerName,
        elapsedMs: Date.now() - startedAt,
        uploadCount: input.uploads.length,
        errorClass: classifyReportContextFailure(error),
        error: error instanceof Error ? error.message : "unknown"
      },
      { scope: "report_context" }
    );
    throw error;
  }

  emitKpi(
    "report_context.preprocess",
    "success",
    {
      provider: options.providerName,
      elapsedMs: Date.now() - startedAt,
      uploadCount: input.uploads.length,
      unitCount: units.length
    },
    { scope: "report_context" }
  );

  let completedUnits = 0;
  let failedUnits = 0;
  const extractedChunks: ReportContextChunk[] = [];
  const effectiveConcurrency = computeAdaptiveConcurrency(options.concurrency, units.length);
  const results = await mapWithConcurrency(units, effectiveConcurrency, async (unit) => {
    try {
      const chunks = await options.extractUnit(unit);
      extractedChunks.push(...chunks);
      completedUnits += 1;
      await input.onUnitProgress?.({
        totalUnits: units.length,
        completedUnits,
        failedUnits,
        elapsedMs: Date.now() - startedAt,
        extractedChunks: deduplicateExtractedChunks(extractedChunks)
      });
      return {
        status: "fulfilled" as const,
        chunks
      };
    } catch (error) {
      failedUnits += 1;
      await input.onUnitProgress?.({
        totalUnits: units.length,
        completedUnits,
        failedUnits,
        elapsedMs: Date.now() - startedAt,
        extractedChunks: deduplicateExtractedChunks(extractedChunks)
      });
      return {
        status: "rejected" as const,
        error
      };
    }
  });

  const fulfilledResults = results.filter((result) => result.status === "fulfilled");
  const chunks = fulfilledResults.flatMap((result) =>
    result.status === "fulfilled" ? result.chunks : []
  );
  if (fulfilledResults.length > 0) {
    const deduped = deduplicateExtractedChunks(chunks);
    if (failedUnits > 0) {
      throw new ReportContextPartialFailureError({
        partialChunks: deduped,
        totalUnits: units.length,
        completedUnits,
        failedUnits
      });
    }
    return deduped;
  }

  const firstError = results.find((result) => result.status === "rejected");
  throw firstError?.status === "rejected"
    ? firstError.error
    : buildReportContextError("no_extractable_content");
}

export class ReportContextPartialFailureError extends Error {
  readonly partialChunks: ReportContextChunk[];
  readonly totalUnits: number;
  readonly completedUnits: number;
  readonly failedUnits: number;

  constructor(input: {
    partialChunks: ReportContextChunk[];
    totalUnits: number;
    completedUnits: number;
    failedUnits: number;
    message?: string;
  }) {
    super(input.message ?? "Report context extraction completed with failed pages.");
    this.name = "ReportContextPartialFailureError";
    this.partialChunks = input.partialChunks;
    this.totalUnits = input.totalUnits;
    this.completedUnits = input.completedUnits;
    this.failedUnits = input.failedUnits;
  }
}

class ReportContextHttpStatusError extends Error {
  readonly statusCode: number;

  constructor(statusCode: number) {
    super(`report context provider returned status ${statusCode}`);
    this.name = "ReportContextHttpStatusError";
    this.statusCode = statusCode;
  }
}

class ReportContextProviderError extends AIProviderError {
  readonly code: ReportContextFailureCode;
  readonly statusCode: number | undefined;

  constructor(code: ReportContextFailureCode, message: string, statusCode?: number) {
    super(message);
    this.name = "ReportContextProviderError";
    this.code = code;
    this.statusCode = statusCode;
  }
}

export interface ReportContextAdapter {
  readonly providerName: string;
  extractChunks(input: {
    sessionId: string;
    uploads: StoredReportContextUpload[];
    onUnitProgress?: (progress: {
      totalUnits: number;
      completedUnits: number;
      failedUnits: number;
      elapsedMs: number;
      extractedChunks: ReportContextChunk[];
    }) => Promise<void> | void;
  }): Promise<ReportContextChunk[]>;
  suggestDraft(input: {
    currentDraft: DraftConsultationDocument;
    selectedChunks: ReportContextChunk[];
  }): Promise<{
    draftDocument: DraftConsultationDocument;
    changedFields: string[];
  }>;
}

export class MockReportContextAdapter implements ReportContextAdapter {
  readonly providerName = "mock-report-context";

  async extractChunks(input: {
    uploads: StoredReportContextUpload[];
    onUnitProgress?: (progress: {
      totalUnits: number;
      completedUnits: number;
      failedUnits: number;
      elapsedMs: number;
      extractedChunks: ReportContextChunk[];
    }) => Promise<void> | void;
  }): Promise<ReportContextChunk[]> {
    const extractedAt = new Date().toISOString();
    return input.uploads.map((upload) =>
      reportContextChunkSchema.parse({
        id: randomUUID(),
        sourceFileName: upload.fileName,
        title: "Uploaded report context",
        category: "Prior report",
        text: `Relevant clinical details were extracted from ${upload.fileName}. Review before applying to the consultation draft.`,
        extractedAt
      })
    );
  }

  async suggestDraft(input: {
    currentDraft: DraftConsultationDocument;
    selectedChunks: ReportContextChunk[];
  }): Promise<{
    draftDocument: DraftConsultationDocument;
    changedFields: string[];
  }> {
    const reportBlock = input.selectedChunks
      .map((chunk) => `${chunk.title}: ${chunk.text}`)
      .join("\n");
    const illnessHistory = [
      input.currentDraft.illnessHistory,
      `Report context:\n${reportBlock}`
    ].filter(Boolean).join("\n\n");

    return {
      draftDocument: {
        ...input.currentDraft,
        illnessHistory
      },
      changedFields: ["illnessHistory"]
    };
  }
}

export class GeminiReportContextAdapter implements ReportContextAdapter {
  readonly providerName: string;
  private readonly apiKey: string;
  private readonly model: string;
  private readonly baseUrl: string;
  private readonly fetchImpl: typeof fetch;

  constructor(options: {
    apiKey: string;
    model?: string;
    baseUrl?: string;
    fetchImpl?: typeof fetch;
    timeoutMs?: number;
    maxRetries?: number;
    retryDelayMs?: number;
    suggestSchemaRetries?: number;
    concurrency?: number;
    maxUnits?: number;
    unitTimeoutMs?: number;
  }) {
    this.apiKey = options.apiKey;
    this.model = options.model ?? "gemini-2.5-flash";
    this.baseUrl = options.baseUrl ?? "https://generativelanguage.googleapis.com/v1beta";
    this.fetchImpl = options.fetchImpl ?? fetch;
    this.timeoutMs = options.timeoutMs ?? 30_000;
    this.maxRetries = options.maxRetries ?? 1;
    this.retryDelayMs = options.retryDelayMs ?? 750;
    this.suggestSchemaRetries = options.suggestSchemaRetries ?? 1;
    this.concurrency = options.concurrency ?? 3;
    this.maxUnits = options.maxUnits ?? 100;
    this.unitTimeoutMs = options.unitTimeoutMs ?? 15_000;
    this.providerName = `${this.model}-report-context`;
  }
  private readonly timeoutMs: number;
  private readonly maxRetries: number;
  private readonly retryDelayMs: number;
  private readonly suggestSchemaRetries: number;
  private readonly concurrency: number;
  private readonly maxUnits: number;
  private readonly unitTimeoutMs: number;

  async extractChunks(input: {
    sessionId: string;
    uploads: StoredReportContextUpload[];
    onUnitProgress?: (progress: {
      totalUnits: number;
      completedUnits: number;
      failedUnits: number;
      elapsedMs: number;
      extractedChunks: ReportContextChunk[];
    }) => Promise<void> | void;
  }): Promise<ReportContextChunk[]> {
    return runSharedReportContextExtraction(input, {
      providerName: this.providerName,
      maxUnits: this.maxUnits,
      concurrency: this.concurrency,
      extractUnit: (unit) => this.extractChunksFromUnit(input.sessionId, unit)
    });
  }

  private async extractChunksFromUnit(
    sessionId: string,
    unit: ReportContextExtractionUnit
  ): Promise<ReportContextChunk[]> {
    const startedAt = Date.now();
    const baseParts = [
      {
        text: [
          reportContextPrompt,
          `Session: ${sessionId}`,
          `Files: ${unit.upload.fileName}`,
          unit.pageNumber ? `PDF page: ${unit.pageNumber}` : "",
          `Extraction unit: ${unit.unitIndex + 1} of ${unit.unitCount}`
        ].join("\n")
      },
      {
        text: unit.pageNumber
          ? `Uploaded file: ${unit.upload.fileName}, page ${unit.pageNumber}`
          : `Uploaded file: ${unit.upload.fileName}`
      },
      {
        inline_data: {
          mime_type: unit.upload.mimeType,
          data: unit.upload.data.toString("base64")
        }
      }
    ];

    const firstPayload = await this.generateJson(baseParts);
    try {
      const strictChunks = this.parseChunksStrict(firstPayload, [unit.upload], unit.pageNumber);
      emitKpi(
        "report_context.extract_parse",
        "success",
        {
          provider: this.providerName,
          elapsedMs: Date.now() - startedAt,
          chunkCount: strictChunks.length,
          strategy: "strict"
        },
        { scope: "report_context" }
      );
      return strictChunks;
    } catch {
      const retryPayload = await this.generateJson([
        {
          text: [
            reportContextRepairPrompt,
            `Session: ${sessionId}`,
            `Files: ${unit.upload.fileName}`,
            unit.pageNumber ? `PDF page: ${unit.pageNumber}` : ""
          ].join("\n")
        },
        ...baseParts
      ]);

      try {
        const retryChunks = this.parseChunksStrict(retryPayload, [unit.upload], unit.pageNumber);
        emitKpi(
          "report_context.extract_parse",
          "success",
          {
            provider: this.providerName,
            elapsedMs: Date.now() - startedAt,
            chunkCount: retryChunks.length,
            strategy: "strict_retry"
          },
          { scope: "report_context" }
        );
        return retryChunks;
      } catch {
        const salvagedFromRetry = this.salvageChunks(retryPayload, [unit.upload], unit.pageNumber);
        if (salvagedFromRetry.length > 0) {
          emitKpi(
            "report_context.extract_parse",
            "success",
            {
              provider: this.providerName,
              elapsedMs: Date.now() - startedAt,
              chunkCount: salvagedFromRetry.length,
              strategy: "salvage_retry"
            },
            { scope: "report_context" }
          );
          return salvagedFromRetry;
        }
        const salvagedFromFirst = this.salvageChunks(firstPayload, [unit.upload], unit.pageNumber);
        if (salvagedFromFirst.length > 0) {
          emitKpi(
            "report_context.extract_parse",
            "success",
            {
              provider: this.providerName,
              elapsedMs: Date.now() - startedAt,
              chunkCount: salvagedFromFirst.length,
              strategy: "salvage_first"
            },
            { scope: "report_context" }
          );
          return salvagedFromFirst;
        }
        const failureCode: ReportContextFailureCode = "no_extractable_content";
        emitKpi(
          "report_context.extract_parse",
          "failure",
          {
            provider: this.providerName,
            elapsedMs: Date.now() - startedAt,
            errorClass: failureCode
          },
          { scope: "report_context" }
        );
        throw buildReportContextError(failureCode);
      }
    }
  }

  async suggestDraft(input: {
    currentDraft: DraftConsultationDocument;
    selectedChunks: ReportContextChunk[];
  }): Promise<{
    draftDocument: DraftConsultationDocument;
    changedFields: string[];
  }> {
    const maxAttempts = this.suggestSchemaRetries + 1;
    for (let attempt = 0; attempt < maxAttempts; attempt += 1) {
      const payload = await this.generateJson(
        this.buildDraftSuggestionParts(input, attempt > 0)
      );
      try {
        return this.parseDraftSuggestionStrict(payload);
      } catch (error) {
        if (!isSchemaMismatchError(error) || attempt === maxAttempts - 1) {
          throw error;
        }
      }
    }
    throw buildReportContextError("provider_schema_mismatch");
  }

  private async generateJson(parts: Array<Record<string, unknown>>): Promise<unknown> {
    const startedAt = Date.now();
    let response: Response;
    try {
      response = await runWithReliability(
        async () => {
          const providerResponse = await this.fetchImpl(
            `${this.baseUrl}/models/${this.model}:generateContent`,
            {
              method: "POST",
              headers: {
                "Content-Type": "application/json",
                "x-goog-api-key": this.apiKey
              },
              body: JSON.stringify({
                generationConfig: {
                  responseMimeType: "application/json"
                },
                contents: [
                  {
                    role: "user",
                    parts
                  }
                ]
              })
            }
          );

          if (!providerResponse.ok) {
            throw new ReportContextHttpStatusError(providerResponse.status);
          }

          return providerResponse;
        },
        {
          operation: "report_context:gemini_generate",
          timeoutMs: this.timeoutMs,
          maxRetries: this.maxRetries,
          retryDelayMs: this.retryDelayMs,
          shouldRetry: isRetryableReportContextError
        }
      );
    } catch (error) {
      const failureCode = classifyReportContextFailure(error);
      emitKpi(
        "report_context.provider_call",
        "failure",
        {
          provider: this.providerName,
          elapsedMs: Date.now() - startedAt,
          ...(error instanceof ReportContextHttpStatusError
            ? { statusCode: error.statusCode }
            : {}),
          errorClass: failureCode,
          error: error instanceof Error ? error.message : "unknown"
        },
        { scope: "report_context" }
      );
      throw buildReportContextError(
        failureCode,
        error instanceof ReportContextHttpStatusError ? error.statusCode : error
      );
    }

    const payload: unknown = await response.json();
    emitKpi(
      "report_context.provider_call",
      "success",
      {
        provider: this.providerName,
        elapsedMs: Date.now() - startedAt
      },
      { scope: "report_context" }
    );
    return parseJsonObject(extractGeminiText(payload));
  }

  private parseChunksStrict(
    payload: unknown,
    uploads: StoredReportContextUpload[],
    defaultPageNumber?: number
  ): ReportContextChunk[] {
    const normalizedPayload = normalizeExtractedChunksPayload(payload);
    const parsed = parseProviderPayload(
      extractedChunksSchema,
      normalizedPayload,
      "Gemini returned an unexpected report context shape."
    );
    return finalizeParsedChunks(parsed.chunks, uploads, defaultPageNumber);
  }

  private salvageChunks(
    payload: unknown,
    uploads: StoredReportContextUpload[],
    defaultPageNumber?: number
  ): ReportContextChunk[] {
    const rawCandidate = extractChunksCandidate(payload);
    if (!Array.isArray(rawCandidate)) {
      return [];
    }

    const recoveredChunks: Array<z.infer<typeof extractedChunksSchema>["chunks"][number]> = [];
    for (const candidate of rawCandidate) {
      let normalizedChunk: unknown;
      try {
        normalizedChunk = normalizeChunk(candidate);
      } catch {
        continue;
      }
      const chunkParsed = z
        .object({
          title: z.string().trim().min(1).max(120),
          category: z.string().trim().min(1).max(80),
          sourceFileName: z.string().trim().min(1).max(255),
          sourceIndex: z.number().int().min(0).max(99).optional(),
          pageNumber: z.number().int().min(1).max(9999).optional(),
          text: z.string().trim().min(1).max(4000)
        })
        .safeParse(normalizedChunk);
      if (chunkParsed.success) {
        recoveredChunks.push(chunkParsed.data);
      }
    }

    if (recoveredChunks.length === 0) {
      return [];
    }
    return finalizeParsedChunks(recoveredChunks, uploads, defaultPageNumber);
  }

  private buildDraftSuggestionParts(
    input: {
      currentDraft: DraftConsultationDocument;
      selectedChunks: ReportContextChunk[];
    },
    repair: boolean
  ): Array<Record<string, unknown>> {
    return [
      {
        text: [
          repair ? draftSuggestionRepairPrompt : draftSuggestionPrompt,
          "Current draft:",
          JSON.stringify(input.currentDraft),
          "Selected report chunks:",
          JSON.stringify(input.selectedChunks.map((chunk) => ({
            title: chunk.title,
            category: chunk.category,
            text: chunk.text
          })))
        ].join("\n\n")
      }
    ];
  }

  private parseDraftSuggestionStrict(payload: unknown): {
    draftDocument: DraftConsultationDocument;
    changedFields: string[];
  } {
    return parseProviderPayload(
      draftSuggestionSchema,
      payload,
      "Gemini returned an unexpected report context draft shape."
    );
  }
}

export class OpenAIReportContextAdapter implements ReportContextAdapter {
  readonly providerName: string;
  private readonly apiKey: string;
  private readonly model: string;
  private readonly baseUrl: string;
  private readonly fetchImpl: typeof fetch;
  private readonly timeoutMs: number;
  private readonly maxRetries: number;
  private readonly retryDelayMs: number;
  private readonly concurrency: number;
  private readonly maxUnits: number;
  private readonly unitTimeoutMs: number;

  constructor(options: {
    apiKey: string;
    model?: string;
    baseUrl?: string;
    fetchImpl?: typeof fetch;
    timeoutMs?: number;
    maxRetries?: number;
    retryDelayMs?: number;
    suggestSchemaRetries?: number;
    concurrency?: number;
    maxUnits?: number;
    unitTimeoutMs?: number;
  }) {
    this.apiKey = options.apiKey;
    this.model = options.model ?? "gpt-5.4-mini";
    this.baseUrl = options.baseUrl ?? "https://api.openai.com/v1";
    this.fetchImpl = options.fetchImpl ?? fetch;
    this.timeoutMs = options.timeoutMs ?? 30_000;
    this.maxRetries = options.maxRetries ?? 1;
    this.retryDelayMs = options.retryDelayMs ?? 750;
    this.suggestSchemaRetries = options.suggestSchemaRetries ?? 1;
    this.concurrency = options.concurrency ?? 3;
    this.maxUnits = options.maxUnits ?? 100;
    this.unitTimeoutMs = options.unitTimeoutMs ?? 15_000;
    this.providerName = `${this.model}-report-context`;
  }
  private readonly suggestSchemaRetries: number;

  async extractChunks(input: {
    sessionId: string;
    uploads: StoredReportContextUpload[];
    onUnitProgress?: (progress: {
      totalUnits: number;
      completedUnits: number;
      failedUnits: number;
      elapsedMs: number;
      extractedChunks: ReportContextChunk[];
    }) => Promise<void> | void;
  }): Promise<ReportContextChunk[]> {
    return runSharedReportContextExtraction(input, {
      providerName: this.providerName,
      maxUnits: this.maxUnits,
      concurrency: this.concurrency,
      extractUnit: (unit) =>
        withUnitTimeout(
          this.extractChunksFromUnit(input.sessionId, unit),
          this.unitTimeoutMs
        )
    });
  }

  async suggestDraft(input: {
    currentDraft: DraftConsultationDocument;
    selectedChunks: ReportContextChunk[];
  }): Promise<{
    draftDocument: DraftConsultationDocument;
    changedFields: string[];
  }> {
    const maxAttempts = this.suggestSchemaRetries + 1;
    for (let attempt = 0; attempt < maxAttempts; attempt += 1) {
      const payload = await this.generateJson(
        this.buildDraftSuggestionInput(input, attempt > 0)
      );
      try {
        return this.parseDraftSuggestionStrict(payload);
      } catch (error) {
        if (!isSchemaMismatchError(error) || attempt === maxAttempts - 1) {
          throw error;
        }
      }
    }
    throw buildReportContextError("provider_schema_mismatch");
  }

  private async extractChunksFromUnit(
    sessionId: string,
    unit: ReportContextExtractionUnit
  ): Promise<ReportContextChunk[]> {
    const startedAt = Date.now();
    const firstPayload = await this.generateJson(
      buildOpenAIExtractionInput(sessionId, unit, false)
    );
    try {
      const strictChunks = this.parseChunksStrict(firstPayload, [unit.upload], unit.pageNumber);
      emitKpi(
        "report_context.extract_parse",
        "success",
        {
          provider: this.providerName,
          elapsedMs: Date.now() - startedAt,
          chunkCount: strictChunks.length,
          strategy: "strict"
        },
        { scope: "report_context" }
      );
      return strictChunks;
    } catch {
      const retryPayload = await this.generateJson(
        buildOpenAIExtractionInput(sessionId, unit, true)
      );
      try {
        const retryChunks = this.parseChunksStrict(retryPayload, [unit.upload], unit.pageNumber);
        emitKpi(
          "report_context.extract_parse",
          "success",
          {
            provider: this.providerName,
            elapsedMs: Date.now() - startedAt,
            chunkCount: retryChunks.length,
            strategy: "strict_retry"
          },
          { scope: "report_context" }
        );
        return retryChunks;
      } catch {
        const salvagedFromRetry = this.salvageChunks(retryPayload, [unit.upload], unit.pageNumber);
        if (salvagedFromRetry.length > 0) {
          emitKpi(
            "report_context.extract_parse",
            "success",
            {
              provider: this.providerName,
              elapsedMs: Date.now() - startedAt,
              chunkCount: salvagedFromRetry.length,
              strategy: "salvage_retry"
            },
            { scope: "report_context" }
          );
          return salvagedFromRetry;
        }
        const salvagedFromFirst = this.salvageChunks(firstPayload, [unit.upload], unit.pageNumber);
        if (salvagedFromFirst.length > 0) {
          emitKpi(
            "report_context.extract_parse",
            "success",
            {
              provider: this.providerName,
              elapsedMs: Date.now() - startedAt,
              chunkCount: salvagedFromFirst.length,
              strategy: "salvage_first"
            },
            { scope: "report_context" }
          );
          return salvagedFromFirst;
        }
        const failureCode: ReportContextFailureCode = "no_extractable_content";
        emitKpi(
          "report_context.extract_parse",
          "failure",
          {
            provider: this.providerName,
            elapsedMs: Date.now() - startedAt,
            errorClass: failureCode
          },
          { scope: "report_context" }
        );
        throw buildReportContextError(failureCode);
      }
    }
  }

  private async generateJson(content: Array<Record<string, unknown>>): Promise<unknown> {
    const startedAt = Date.now();
    let response: Response;
    try {
      response = await runWithReliability(
        async () => {
          const providerResponse = await this.fetchImpl(`${this.baseUrl}/responses`, {
            method: "POST",
            headers: {
              "Content-Type": "application/json",
              Authorization: `Bearer ${this.apiKey}`
            },
            body: JSON.stringify({
              model: this.model,
              text: { format: { type: "json_object" } },
              input: [{ role: "user", content }]
            })
          });

          if (!providerResponse.ok) {
            throw new ReportContextHttpStatusError(providerResponse.status);
          }

          return providerResponse;
        },
        {
          operation: "report_context:openai_generate",
          timeoutMs: this.timeoutMs,
          maxRetries: this.maxRetries,
          retryDelayMs: this.retryDelayMs,
          shouldRetry: isRetryableReportContextError
        }
      );
    } catch (error) {
      const failureCode = classifyReportContextFailure(error);
      emitKpi(
        "report_context.provider_call",
        "failure",
        {
          provider: this.providerName,
          elapsedMs: Date.now() - startedAt,
          ...(error instanceof ReportContextHttpStatusError
            ? { statusCode: error.statusCode }
            : {}),
          errorClass: failureCode,
          error: error instanceof Error ? error.message : "unknown"
        },
        { scope: "report_context" }
      );
      throw buildReportContextError(
        failureCode,
        error instanceof ReportContextHttpStatusError ? error.statusCode : error
      );
    }

    const payload: unknown = await response.json();
    emitKpi(
      "report_context.provider_call",
      "success",
      {
        provider: this.providerName,
        elapsedMs: Date.now() - startedAt
      },
      { scope: "report_context" }
    );
    return parseJsonObject(extractOpenAIText(payload));
  }

  private parseChunksStrict(
    payload: unknown,
    uploads: StoredReportContextUpload[],
    defaultPageNumber?: number
  ): ReportContextChunk[] {
    const normalizedPayload = normalizeExtractedChunksPayload(payload);
    const parsed = parseProviderPayload(
      extractedChunksSchema,
      normalizedPayload,
      "OpenAI returned an unexpected report context shape."
    );
    return finalizeParsedChunks(parsed.chunks, uploads, defaultPageNumber);
  }

  private salvageChunks(
    payload: unknown,
    uploads: StoredReportContextUpload[],
    defaultPageNumber?: number
  ): ReportContextChunk[] {
    const rawCandidate = extractChunksCandidate(payload);
    if (!Array.isArray(rawCandidate)) {
      return [];
    }

    const recoveredChunks: Array<z.infer<typeof extractedChunksSchema>["chunks"][number]> = [];
    for (const candidate of rawCandidate) {
      let normalizedChunk: unknown;
      try {
        normalizedChunk = normalizeChunk(candidate);
      } catch {
        continue;
      }
      const chunkParsed = z
        .object({
          title: z.string().trim().min(1).max(120),
          category: z.string().trim().min(1).max(80),
          sourceFileName: z.string().trim().min(1).max(255),
          sourceIndex: z.number().int().min(0).max(99).optional(),
          pageNumber: z.number().int().min(1).max(9999).optional(),
          text: z.string().trim().min(1).max(4000)
        })
        .safeParse(normalizedChunk);
      if (chunkParsed.success) {
        recoveredChunks.push(chunkParsed.data);
      }
    }

    if (recoveredChunks.length === 0) {
      return [];
    }
    return finalizeParsedChunks(recoveredChunks, uploads, defaultPageNumber);
  }

  private buildDraftSuggestionInput(
    input: {
      currentDraft: DraftConsultationDocument;
      selectedChunks: ReportContextChunk[];
    },
    repair: boolean
  ): Array<Record<string, unknown>> {
    return [
      {
        type: "input_text",
        text: [
          repair ? draftSuggestionRepairPrompt : draftSuggestionPrompt,
          "Current draft:",
          JSON.stringify(input.currentDraft),
          "Selected report chunks:",
          JSON.stringify(input.selectedChunks.map((chunk) => ({
            title: chunk.title,
            category: chunk.category,
            text: chunk.text
          })))
        ].join("\n\n")
      }
    ];
  }

  private parseDraftSuggestionStrict(payload: unknown): {
    draftDocument: DraftConsultationDocument;
    changedFields: string[];
  } {
    return parseProviderPayload(
      draftSuggestionSchema,
      payload,
      "OpenAI returned an unexpected report context draft shape."
    );
  }
}

export class FallbackReportContextAdapter implements ReportContextAdapter {
  readonly providerName: string;

  constructor(private readonly options: {
    primary: ReportContextAdapter;
    fallback: ReportContextAdapter;
  }) {
    this.providerName = `${options.primary.providerName}->${options.fallback.providerName}`;
  }

  async extractChunks(input: {
    sessionId: string;
    uploads: StoredReportContextUpload[];
    onUnitProgress?: (progress: {
      totalUnits: number;
      completedUnits: number;
      failedUnits: number;
      elapsedMs: number;
      extractedChunks: ReportContextChunk[];
    }) => Promise<void> | void;
  }): Promise<ReportContextChunk[]> {
    try {
      const result = await this.options.primary.extractChunks(input);
      emitReportContextFallbackKpi({
        adapter: this,
        operation: "extract_chunks",
        fallbackUsed: false,
        status: "success",
        provider: this.options.primary.providerName
      });
      return result;
    } catch (error) {
      if (!isRetryableReportContextFallbackError(error)) {
        emitReportContextFallbackKpi({
          adapter: this,
          operation: "extract_chunks",
          fallbackUsed: false,
          status: "failure",
          provider: this.options.primary.providerName,
          error
        });
        throw error;
      }
      try {
        const fallbackResult = await this.options.fallback.extractChunks(input);
        emitReportContextFallbackKpi({
          adapter: this,
          operation: "extract_chunks",
          fallbackUsed: true,
          status: "success",
          provider: this.options.fallback.providerName
        });
        return fallbackResult;
      } catch (fallbackError) {
        emitReportContextFallbackKpi({
          adapter: this,
          operation: "extract_chunks",
          fallbackUsed: true,
          status: "failure",
          provider: this.options.fallback.providerName,
          error: fallbackError
        });
        throw fallbackError;
      }
    }
  }

  async suggestDraft(input: {
    currentDraft: DraftConsultationDocument;
    selectedChunks: ReportContextChunk[];
  }): Promise<{
    draftDocument: DraftConsultationDocument;
    changedFields: string[];
  }> {
    try {
      const result = await this.options.primary.suggestDraft(input);
      emitReportContextFallbackKpi({
        adapter: this,
        operation: "suggest_draft",
        fallbackUsed: false,
        status: "success",
        provider: this.options.primary.providerName
      });
      return result;
    } catch (error) {
      if (!isRetryableReportContextFallbackError(error)) {
        emitReportContextFallbackKpi({
          adapter: this,
          operation: "suggest_draft",
          fallbackUsed: false,
          status: "failure",
          provider: this.options.primary.providerName,
          error
        });
        throw error;
      }
      try {
        const fallbackResult = await this.options.fallback.suggestDraft(input);
        emitReportContextFallbackKpi({
          adapter: this,
          operation: "suggest_draft",
          fallbackUsed: true,
          status: "success",
          provider: this.options.fallback.providerName
        });
        return fallbackResult;
      } catch (fallbackError) {
        emitReportContextFallbackKpi({
          adapter: this,
          operation: "suggest_draft",
          fallbackUsed: true,
          status: "failure",
          provider: this.options.fallback.providerName,
          error: fallbackError
        });
        throw fallbackError;
      }
    }
  }
}

function emitReportContextFallbackKpi(input: {
  adapter: FallbackReportContextAdapter;
  operation: "extract_chunks" | "suggest_draft";
  fallbackUsed: boolean;
  status: "success" | "failure";
  provider: string;
  error?: unknown;
}): void {
  emitKpi(
    "report_context.fallback_path",
    input.status,
    {
      provider: input.provider,
      fallbackProviderChain: input.adapter.providerName,
      operation: input.operation,
      fallbackUsed: input.fallbackUsed,
      ...(input.error instanceof Error ? { error: input.error.message } : {})
    },
    { scope: "report_context" }
  );
}

async function expandReportContextUploads(
  uploads: StoredReportContextUpload[],
  maxUnits: number
): Promise<ReportContextExtractionUnit[]> {
  const expandedGroups = await Promise.all(
    uploads.map((upload) => expandReportContextUpload(upload))
  );
  const uploadsWithUnits = expandedGroups.flat();
  // Prioritize image units for faster first visible extraction output.
  uploadsWithUnits.sort((left, right) => {
    const leftIsImage = left.upload.mimeType.startsWith("image/");
    const rightIsImage = right.upload.mimeType.startsWith("image/");
    if (leftIsImage === rightIsImage) {
      return 0;
    }
    return leftIsImage ? -1 : 1;
  });

  if (uploadsWithUnits.length > maxUnits) {
    throw buildReportContextError("too_many_pages", {
      requestedUnits: uploadsWithUnits.length,
      maxUnits
    });
  }

  return uploadsWithUnits.map((unit, index) => ({
    ...unit,
    unitIndex: index,
    unitCount: uploadsWithUnits.length
  }));
}

async function expandReportContextUpload(
  upload: StoredReportContextUpload
): Promise<Array<Omit<ReportContextExtractionUnit, "unitIndex" | "unitCount">>> {
  if (upload.mimeType !== "application/pdf") {
    return [{ upload }];
  }

  try {
    const sourcePdf = await PDFDocument.load(upload.data, {
      ignoreEncryption: true
    });
    const pages = sourcePdf.getPages();
    if (pages.length <= 1) {
      return pages.length === 1 ? [{ upload, pageNumber: 1 }] : [{ upload }];
    }

    const pageUploads = await Promise.all(
      pages.map(async (_page, pageIndex) => {
        const pagePdf = await PDFDocument.create();
        const [copiedPage] = await pagePdf.copyPages(sourcePdf, [pageIndex]);
        pagePdf.addPage(copiedPage);
        const pageBytes = await pagePdf.save();
        return {
          upload: {
            ...upload,
            data: Buffer.from(pageBytes),
            sizeBytes: pageBytes.byteLength
          },
          pageNumber: pageIndex + 1
        };
      })
    );

    return pageUploads;
  } catch {
    return [{ upload }];
  }
}

async function mapWithConcurrency<T, R>(
  items: T[],
  concurrency: number,
  mapper: (item: T) => Promise<R>
): Promise<R[]> {
  const results: R[] = new Array<R>(items.length);
  let nextIndex = 0;
  const workerCount = Math.min(Math.max(concurrency, 1), items.length);

  await Promise.all(
    Array.from({ length: workerCount }, async () => {
      while (nextIndex < items.length) {
        const currentIndex = nextIndex;
        nextIndex += 1;
        results[currentIndex] = await mapper(items[currentIndex]!);
      }
    })
  );

  return results;
}

function computeAdaptiveConcurrency(
  configuredConcurrency: number,
  totalUnits: number
): number {
  const bounded = Math.min(Math.max(configuredConcurrency, 1), 4);
  return Math.min(bounded, totalUnits);
}

async function withUnitTimeout<T>(promise: Promise<T>, timeoutMs: number): Promise<T> {
  let timer: NodeJS.Timeout | null = null;
  const timeoutPromise = new Promise<never>((_resolve, reject) => {
    timer = setTimeout(() => {
      reject(buildReportContextError("provider_timeout"));
    }, timeoutMs);
  });

  try {
    return await Promise.race([promise, timeoutPromise]);
  } finally {
    if (timer) {
      clearTimeout(timer);
    }
  }
}

function finalizeParsedChunks(
  chunks: Array<{
    title: string;
    category: string;
    sourceFileName: string;
    sourceIndex?: number | undefined;
    pageNumber?: number | undefined;
    text: string;
  }>,
  uploads: StoredReportContextUpload[],
  defaultPageNumber?: number
): ReportContextChunk[] {
  const extractedAt = new Date().toISOString();
  const uploadedFileNames = uploads.map((upload) => upload.fileName);
  const uploadIndexByFileName = new Map(
    uploadedFileNames.map((fileName, index) => [fileName, index])
  );

  const normalizedChunks: ReportContextChunk[] = [];
  for (const chunk of chunks) {
    try {
      const sourceFileName = parseUploadedSourceFileName(
        chunk.sourceFileName,
        uploadedFileNames
      );
      normalizedChunks.push(
        reportContextChunkSchema.parse({
          id: randomUUID(),
          sourceFileName,
          sourceIndex: uploadIndexByFileName.get(sourceFileName),
          pageNumber: chunk.pageNumber ?? defaultPageNumber,
          title: chunk.title,
          category: normalizeClinicalCategory(chunk.category, chunk.title, chunk.text),
          text: chunk.text,
          extractedAt
        })
      );
    } catch {
      continue;
    }
  }

  return deduplicateExtractedChunks(normalizedChunks);
}

function parseProviderPayload<T>(
  schema: z.ZodType<T>,
  payload: unknown,
  message: string
): T {
  const parsed = schema.safeParse(payload);

  if (!parsed.success) {
    throw buildReportContextError("provider_schema_mismatch", message);
  }

  return parsed.data;
}

function parseUploadedSourceFileName(
  sourceFileName: string,
  uploadedFileNames: string[]
): string {
  const exactMatch = uploadedFileNames.find((fileName) => fileName === sourceFileName);
  if (exactMatch) {
    return exactMatch;
  }

  const normalizedSource = normalizeFileName(sourceFileName);
  const normalizedMatch = uploadedFileNames.find(
    (fileName) => normalizeFileName(fileName) === normalizedSource
  );

  if (normalizedMatch) {
    return normalizedMatch;
  }

  const basenameSource = basenameFileName(normalizedSource);
  const basenameMatch = uploadedFileNames.find(
    (fileName) => basenameFileName(normalizeFileName(fileName)) === basenameSource
  );

  if (basenameMatch) {
    return basenameMatch;
  }

  if (uploadedFileNames.length === 1) {
    return uploadedFileNames[0]!;
  }

  throw buildReportContextError(
    "source_file_mapping_failed",
    sourceFileName
  );
}

function normalizeExtractedChunksPayload(payload: unknown): unknown {
  const chunksCandidate = extractChunksCandidate(payload);
  const chunksArray = parseProviderPayload(
    z.array(z.unknown()),
    chunksCandidate,
    "Gemini returned an unexpected report context shape."
  );

  return {
    chunks: chunksArray.map((chunk) => normalizeChunk(chunk))
  };
}

function buildOpenAIExtractionInput(
  sessionId: string,
  unit: ReportContextExtractionUnit,
  useRepairPrompt: boolean
): Array<Record<string, unknown>> {
  const prompt = useRepairPrompt ? reportContextRepairPrompt : reportContextPrompt;
  return [
    {
      type: "input_text",
      text: [
        prompt,
        `Session: ${sessionId}`,
        `Files: ${unit.upload.fileName}`,
        unit.pageNumber ? `PDF page: ${unit.pageNumber}` : "",
        `Extraction unit: ${unit.unitIndex + 1} of ${unit.unitCount}`
      ].join("\n")
    },
    {
      type: "input_text",
      text: unit.pageNumber
        ? `Uploaded file: ${unit.upload.fileName}, page ${unit.pageNumber}`
        : `Uploaded file: ${unit.upload.fileName}`
    },
    unit.upload.mimeType.startsWith("image/")
      ? {
          type: "input_image",
          image_url: `data:${unit.upload.mimeType};base64,${unit.upload.data.toString("base64")}`
        }
      : {
          type: "input_file",
          filename: unit.upload.fileName,
          file_data: `data:${unit.upload.mimeType};base64,${unit.upload.data.toString("base64")}`
        }
  ];
}

function extractChunksCandidate(payload: unknown): unknown {
  if (Array.isArray(payload)) {
    return payload;
  }

  if (!payload || typeof payload !== "object") {
    return payload;
  }

  const record = payload as Record<string, unknown>;
  const direct = record.chunks;
  if (direct !== undefined) {
    return direct;
  }

  const nestedKeys = ["data", "result", "response", "output"] as const;
  for (const key of nestedKeys) {
    const nestedCandidate = extractChunksCandidate(record[key]);
    if (nestedCandidate !== undefined) {
      return nestedCandidate;
    }
  }

  const reportContextChunks = record.reportContextChunks;
  if (Array.isArray(reportContextChunks)) {
    return reportContextChunks;
  }

  const items = record.items;
  if (Array.isArray(items)) {
    return items;
  }

  for (const value of Object.values(record)) {
    if (!Array.isArray(value)) {
      continue;
    }

    if (value.length === 0) {
      return value;
    }

    const first = value[0];
    if (first && typeof first === "object" && !Array.isArray(first)) {
      return value;
    }
  }

  return undefined;
}

function normalizeChunk(chunk: unknown): unknown {
  const record = unwrapObject(chunk);
  const source = maybeObject(record.source) ?? maybeObject(record.metadata) ?? {};
  const sourceFileName = pickString([
    record.sourceFileName,
    record.source_file_name,
    record.fileName,
    record.filename,
    record.sourceFile,
    source.fileName,
    source.filename
  ]);

  return {
    title: pickString([record.title, record.heading, record.name]),
    category: pickString([record.category, record.type, record.section, "Report"]),
    sourceIndex: pickOptionalInteger([
      record.sourceIndex,
      record.source_index,
      source.index
    ]),
    pageNumber: pickOptionalInteger([
      record.pageNumber,
      record.page_number,
      record.page,
      source.pageNumber,
      source.page
    ]),
    sourceFileName,
    text: pickString([record.text, record.content, record.summary, record.value])
  };
}

function unwrapObject(value: unknown): Record<string, unknown> {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    throw buildReportContextError("provider_schema_mismatch");
  }

  return value as Record<string, unknown>;
}

function maybeObject(value: unknown): Record<string, unknown> | null {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    return null;
  }

  return value as Record<string, unknown>;
}

function pickString(candidates: Array<unknown>): string {
  for (const candidate of candidates) {
    if (typeof candidate !== "string") {
      continue;
    }

    const trimmed = candidate.trim();
    if (trimmed.length > 0) {
      return trimmed;
    }
  }

  throw buildReportContextError("provider_schema_mismatch");
}

function classifyReportContextFailure(error: unknown): ReportContextFailureCode {
  if (error instanceof ReportContextHttpStatusError) {
    return "provider_http_error";
  }

  const message =
    error instanceof Error ? error.message.toLowerCase() : String(error).toLowerCase();
  if (message.includes("timed out after")) {
    return "provider_timeout";
  }
  if (message.includes("status")) {
    return "provider_http_error";
  }
  if (message.includes("unknown source file")) {
    return "source_file_mapping_failed";
  }
  if (message.includes("unexpected report context shape")) {
    return "provider_schema_mismatch";
  }
  if (message.includes("too many report pages")) {
    return "too_many_pages";
  }
  return "provider_transport_error";
}

function isRetryableReportContextError(error: unknown): boolean {
  if (error instanceof ReportContextHttpStatusError) {
    return error.statusCode === 429 || error.statusCode === 408 || error.statusCode >= 500;
  }

  return true;
}

function isRetryableReportContextFallbackError(error: unknown): boolean {
  if (error instanceof ReportContextProviderError) {
    if (
      error.code === "provider_timeout"
      || error.code === "provider_transport_error"
      || error.code === "provider_schema_mismatch"
    ) {
      return true;
    }
    if (error.code === "provider_http_error") {
      if (typeof error.statusCode !== "number") {
        return true;
      }
      return (
        error.statusCode === 408
        || error.statusCode === 429
        || error.statusCode >= 500
      );
    }
    return false;
  }

  const message =
    error instanceof Error ? error.message.toUpperCase() : String(error).toUpperCase();
  if (message.includes("REPORT_CONTEXT_PROVIDER_TIMEOUT")) {
    return true;
  }
  if (message.includes("REPORT_CONTEXT_PROVIDER_TRANSPORT_ERROR")) {
    return true;
  }
  if (message.includes("REPORT_CONTEXT_PROVIDER_SCHEMA_MISMATCH")) {
    return true;
  }
  if (message.includes("REPORT_CONTEXT_PROVIDER_HTTP_ERROR")) {
    const status = /\((\d{3})\)/.exec(message)?.[1];
    if (!status) {
      return true;
    }
    const statusCode = Number.parseInt(status, 10);
    return statusCode === 408 || statusCode === 429 || statusCode >= 500;
  }
  return false;
}

function isSchemaMismatchError(error: unknown): boolean {
  if (error instanceof ReportContextProviderError) {
    return error.code === "provider_schema_mismatch";
  }
  const message = error instanceof Error ? error.message : String(error);
  return message.toUpperCase().includes("REPORT_CONTEXT_PROVIDER_SCHEMA_MISMATCH");
}

function buildReportContextError(
  code: ReportContextFailureCode,
  detail?: unknown
): AIProviderError {
  switch (code) {
    case "provider_timeout":
      return new ReportContextProviderError(
        "provider_timeout",
        "REPORT_CONTEXT_PROVIDER_TIMEOUT: report extraction timed out."
      );
    case "provider_http_error":
      return new ReportContextProviderError(
        "provider_http_error",
        `REPORT_CONTEXT_PROVIDER_HTTP_ERROR: provider returned a failed status${typeof detail === "number" ? ` (${detail})` : ""}.`,
        typeof detail === "number" ? detail : undefined
      );
    case "provider_schema_mismatch":
      return new ReportContextProviderError(
        "provider_schema_mismatch",
        "REPORT_CONTEXT_PROVIDER_SCHEMA_MISMATCH: provider returned an unexpected format."
      );
    case "source_file_mapping_failed":
      return new ReportContextProviderError(
        "source_file_mapping_failed",
        `REPORT_CONTEXT_SOURCE_FILE_MAPPING_FAILED: provider referenced unknown file${typeof detail === "string" ? ` (${detail})` : ""}.`
      );
    case "no_extractable_content":
      return new ReportContextProviderError(
        "no_extractable_content",
        "REPORT_CONTEXT_NO_EXTRACTABLE_CONTENT: no valid report chunks were extracted."
      );
    case "provider_transport_error":
      return new ReportContextProviderError(
        "provider_transport_error",
        `REPORT_CONTEXT_PROVIDER_TRANSPORT_ERROR: provider request failed${detail instanceof Error ? ` (${detail.message})` : ""}.`
      );
    case "too_many_pages":
      if (
        detail &&
        typeof detail === "object" &&
        "requestedUnits" in detail &&
        "maxUnits" in detail
      ) {
        const parsed = detail as TooManyPagesDetail;
        return new ReportContextProviderError(
          "too_many_pages",
          `REPORT_CONTEXT_TOO_MANY_PAGES: report includes ${parsed.requestedUnits} pages/files but the limit is ${parsed.maxUnits}.`
        );
      }
      return new ReportContextProviderError(
        "too_many_pages",
        `REPORT_CONTEXT_TOO_MANY_PAGES: too many report pages were uploaded${typeof detail === "number" ? ` (maximum ${detail})` : ""}.`
      );
  }
}

function pickOptionalInteger(candidates: Array<unknown>): number | undefined {
  for (const candidate of candidates) {
    if (typeof candidate === "number" && Number.isInteger(candidate)) {
      return candidate;
    }

    if (typeof candidate !== "string") {
      continue;
    }

    const normalized = candidate.trim();
    if (!/^\d+$/.test(normalized)) {
      continue;
    }

    const parsed = Number.parseInt(normalized, 10);
    if (Number.isInteger(parsed)) {
      return parsed;
    }
  }

  return undefined;
}

const CATEGORY_SYNONYMS: Array<{
  normalized: string;
  keywords: string[];
}> = [
  { normalized: "Lab", keywords: ["lab", "laboratory", "blood", "cbc", "hba1c", "lft", "rft", "thyroid", "biochemistry"] },
  { normalized: "Imaging", keywords: ["imaging", "xray", "x-ray", "ct", "mri", "ultrasound", "usg", "scan", "radiology"] },
  { normalized: "Diagnosis", keywords: ["diagnosis", "diagnoses", "impression", "assessment", "condition"] },
  { normalized: "Medication History", keywords: ["medication", "medicine", "drug", "tablet", "dose", "dosage", "prescription"] },
  { normalized: "Allergy", keywords: ["allergy", "allergic", "hypersensitivity"] },
  { normalized: "Procedure", keywords: ["procedure", "surgery", "operative", "operation", "intervention"] },
  { normalized: "Risk/Caution", keywords: ["risk", "caution", "warning", "abnormal", "critical", "elevated", "low", "urgent"] },
  { normalized: "Follow-up", keywords: ["follow up", "follow-up", "review", "revisit", "next visit", "after"] }
];

function normalizeClinicalCategory(
  category: string,
  title: string,
  text: string
): string {
  const normalizedInput = `${category} ${title} ${text}`.toLowerCase();

  for (const entry of CATEGORY_SYNONYMS) {
    if (entry.keywords.some((keyword) => normalizedInput.includes(keyword))) {
      return entry.normalized;
    }
  }

  const trimmedCategory = category.trim();
  if (!trimmedCategory || /^report$/i.test(trimmedCategory) || /^general$/i.test(trimmedCategory)) {
    return "Other";
  }

  return trimmedCategory;
}

function deduplicateExtractedChunks(chunks: ReportContextChunk[]): ReportContextChunk[] {
  const bySignature = new Map<string, ReportContextChunk>();

  for (const chunk of chunks) {
    const signature = [
      chunk.sourceFileName.trim().toLowerCase(),
      (chunk.pageNumber ?? "").toString(),
      chunk.title.trim().toLowerCase(),
      chunk.text.trim().toLowerCase().replace(/\s+/g, " ")
    ].join("|");
    const existing = bySignature.get(signature);

    if (!existing) {
      bySignature.set(signature, chunk);
      continue;
    }

    const existingQuality = existing.text.length + existing.title.length;
    const nextQuality = chunk.text.length + chunk.title.length;

    if (nextQuality > existingQuality) {
      bySignature.set(signature, chunk);
    }
  }

  return Array.from(bySignature.values());
}

function normalizeFileName(value: string): string {
  return value.trim().toLowerCase().replace(/\\/g, "/");
}

function basenameFileName(value: string): string {
  const slashIndex = value.lastIndexOf("/");
  return slashIndex >= 0 ? value.slice(slashIndex + 1) : value;
}

function extractGeminiText(payload: unknown): string {
  const parsed = z.object({
    candidates: z.array(z.object({
      content: z.object({
        parts: z.array(z.object({ text: z.string() }))
      })
    })).min(1)
  }).parse(payload);
  const firstCandidate = parsed.candidates[0]!;
  const text = firstCandidate.content.parts.map((part) => part.text).join("\n");

  if (!text) {
    throw new AIProviderError("Gemini report context returned no text.");
  }

  return text;
}

function extractOpenAIText(payload: unknown): string {
  if (!payload || typeof payload !== "object") {
    throw new AIProviderError("OpenAI report context returned no text.");
  }
  const record = payload as Record<string, unknown>;
  if (typeof record.output_text === "string" && record.output_text.trim().length > 0) {
    return record.output_text;
  }

  const output = Array.isArray(record.output) ? record.output : [];
  const parts: string[] = [];
  for (const item of output) {
    if (!item || typeof item !== "object") {
      continue;
    }
    const itemRecord = item as Record<string, unknown>;
    const content = Array.isArray(itemRecord.content) ? itemRecord.content : [];
    for (const contentPart of content) {
      if (!contentPart || typeof contentPart !== "object") {
        continue;
      }
      const contentRecord = contentPart as Record<string, unknown>;
      const text = contentRecord.text;
      if (typeof text === "string" && text.trim().length > 0) {
        parts.push(text);
      }
    }
  }

  const combined = parts.join("\n").trim();
  if (combined.length === 0) {
    throw new AIProviderError("OpenAI report context returned no text.");
  }
  return combined;
}

function parseJsonObject(text: string): unknown {
  const trimmed = text.trim().replace(/^```json\s*/i, "").replace(/^```\s*/i, "").replace(/```$/i, "").trim();

  try {
    return JSON.parse(trimmed);
  } catch (error) {
    throw new AIProviderError(
      error instanceof Error ? error.message : "Unable to parse report context JSON."
    );
  }
}
