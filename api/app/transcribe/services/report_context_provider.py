from __future__ import annotations

import base64
import json
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Callable, Sequence

import fitz
import httpx
from pydantic import BaseModel, Field, ValidationError

from api.app.core.config import Settings
from api.app.transcribe.schemas.common import ReportContextChunk
from api.app.transcribe.schemas.sessions import DraftConsultationDocument

OPENAI_BASE_URL = "https://api.openai.com/v1"

EXTRACTION_PROMPT = """
You extract useful clinical context from physical reports uploaded during a consultation.
Return JSON only with this exact shape:
{
  "chunks": [
    {
      "title": string,
      "category": string,
      "text": string
    }
  ]
}
Create concise chunks that a doctor can choose from. Prefer clinically meaningful groups such as diagnosis, history, lab abnormalities, imaging findings, medications, allergy, cautions, follow-up, or prior procedure.
Do not invent facts. Ignore billing details, page headers, footers, and duplicate boilerplate.
Use clear clinical English.
""".strip()

EXTRACTION_REPAIR_PROMPT = """
Return strict JSON only. Do not include markdown fences, prose, or comments.
The payload must be exactly:
{
  "chunks": [
    {
      "title": string,
      "category": string,
      "text": string
    }
  ]
}
If unsure, still return best-effort chunks with non-empty title/category/text.
""".strip()

DRAFT_SUGGESTION_PROMPT = """
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
""".strip()


class ExtractedChunkPayload(BaseModel):
    title: str = Field(min_length=1, max_length=120)
    category: str = Field(min_length=1, max_length=80)
    text: str = Field(min_length=1, max_length=4000)


class ExtractionResponsePayload(BaseModel):
    chunks: list[ExtractedChunkPayload] = Field(default_factory=list, max_length=20)


class DraftSuggestionPayload(BaseModel):
    draftDocument: DraftConsultationDocument
    changedFields: list[str] = Field(default_factory=list)


@dataclass(slots=True)
class StoredReportContextUpload:
    id: str
    file_name: str
    mime_type: str
    size_bytes: int
    data: bytes


@dataclass(slots=True)
class ReportContextProgressUpdate:
    total_units: int
    completed_units: int
    failed_units: int
    extracted_chunks: list[ReportContextChunk]


@dataclass(slots=True)
class ReportContextExtractionUnit:
    upload: StoredReportContextUpload
    source_index: int
    unit_index: int
    total_units: int
    page_number: int | None = None
    image_bytes: bytes | None = None
    extracted_text: str | None = None


class ReportContextProviderError(RuntimeError):
    pass


class ReportContextPartialFailureError(ReportContextProviderError):
    def __init__(
        self,
        partial_chunks: list[ReportContextChunk],
        total_units: int,
        completed_units: int,
        failed_units: int,
    ) -> None:
        super().__init__("Report context extraction completed with skipped pages.")
        self.partial_chunks = partial_chunks
        self.total_units = total_units
        self.completed_units = completed_units
        self.failed_units = failed_units


class BaseReportContextProvider:
    provider_name: str

    async def extract_chunks(
        self,
        session_id: str,
        uploads: Sequence[StoredReportContextUpload],
        on_progress: Callable[[ReportContextProgressUpdate], Any] | None = None,
    ) -> list[ReportContextChunk]:
        raise NotImplementedError

    async def suggest_draft(
        self,
        session_id: str,
        current_draft: DraftConsultationDocument,
        selected_chunks: Sequence[ReportContextChunk],
    ) -> dict[str, Any]:
        raise NotImplementedError


class MockReportContextProvider(BaseReportContextProvider):
    provider_name = "mock-report-context"

    async def extract_chunks(
        self,
        session_id: str,
        uploads: Sequence[StoredReportContextUpload],
        on_progress: Callable[[ReportContextProgressUpdate], Any] | None = None,
    ) -> list[ReportContextChunk]:
        extracted_at = datetime.now(UTC)
        chunks = [
            ReportContextChunk(
                id=str(uuid.uuid4()),
                sourceFileName=upload.file_name,
                sourceIndex=index,
                title="Uploaded report context",
                category="Prior report",
                text=f"Relevant clinical details were extracted from {upload.file_name}.",
                extractedAt=extracted_at,
                label="Uploaded report context",
            )
            for index, upload in enumerate(uploads)
        ]
        if on_progress is not None:
            await _maybe_await(
                on_progress(
                    ReportContextProgressUpdate(
                        total_units=len(chunks),
                        completed_units=len(chunks),
                        failed_units=0,
                        extracted_chunks=chunks,
                    )
                )
            )
        return chunks

    async def suggest_draft(
        self,
        session_id: str,
        current_draft: DraftConsultationDocument,
        selected_chunks: Sequence[ReportContextChunk],
    ) -> dict[str, Any]:
        illness_history = "\n\n".join(
            part
            for part in [
                current_draft.illnessHistory,
                "\n".join(f"{chunk.title}: {chunk.text}" for chunk in selected_chunks),
            ]
            if part
        )
        draft = current_draft.model_copy(deep=True)
        draft.illnessHistory = illness_history or current_draft.illnessHistory
        return {
            "draftDocument": draft.model_dump(exclude_none=True),
            "changedFields": ["illnessHistory"] if illness_history else [],
        }


class OpenAIReportContextProvider(BaseReportContextProvider):
    def __init__(self, settings: Settings) -> None:
        if not settings.openai_api_key:
            raise ReportContextProviderError(
                "OPENAI_API_KEY is required when report context uses OpenAI."
            )
        self.api_key = settings.openai_api_key
        self.base_url = (settings.openai_base_url or OPENAI_BASE_URL).rstrip("/")
        self.model = settings.report_context_openai_model
        self.timeout_seconds = settings.report_context_timeout_seconds
        self.max_units = settings.report_context_max_units
        self.provider_name = f"openai:{self.model}"

    async def extract_chunks(
        self,
        session_id: str,
        uploads: Sequence[StoredReportContextUpload],
        on_progress: Callable[[ReportContextProgressUpdate], Any] | None = None,
    ) -> list[ReportContextChunk]:
        units = _expand_units(uploads, self.max_units)
        extracted_chunks: list[ReportContextChunk] = []
        completed_units = 0
        failed_units = 0
        for unit in units:
            try:
                extracted_chunks.extend(await self._extract_unit(session_id, unit))
                completed_units += 1
            except Exception:
                failed_units += 1
            if on_progress is not None:
                await _maybe_await(
                    on_progress(
                        ReportContextProgressUpdate(
                            total_units=len(units),
                            completed_units=completed_units,
                            failed_units=failed_units,
                            extracted_chunks=list(extracted_chunks),
                        )
                    )
                )
        deduped = _dedupe_chunks(extracted_chunks)
        if not deduped:
            raise ReportContextProviderError(
                "Report context extraction returned no usable clinical content."
            )
        if failed_units:
            raise ReportContextPartialFailureError(
                partial_chunks=deduped,
                total_units=len(units),
                completed_units=completed_units,
                failed_units=failed_units,
            )
        return deduped

    async def suggest_draft(
        self,
        session_id: str,
        current_draft: DraftConsultationDocument,
        selected_chunks: Sequence[ReportContextChunk],
    ) -> dict[str, Any]:
        payload = await self._generate_json(
            [
                {
                    "type": "input_text",
                    "text": "\n\n".join(
                        [
                            DRAFT_SUGGESTION_PROMPT,
                            "Current draft:",
                            json.dumps(
                                current_draft.model_dump(exclude_none=True),
                                ensure_ascii=True,
                            ),
                            "Selected report chunks:",
                            json.dumps(
                                [
                                    {
                                        "title": chunk.title,
                                        "category": chunk.category,
                                        "text": chunk.text,
                                    }
                                    for chunk in selected_chunks
                                ],
                                ensure_ascii=True,
                            ),
                        ]
                    ),
                }
            ]
        )
        try:
            parsed = DraftSuggestionPayload.model_validate(payload)
        except ValidationError as exc:
            raise ReportContextProviderError(
                f"OpenAI returned an unexpected draft suggestion payload: {exc}"
            ) from exc
        return {
            "draftDocument": parsed.draftDocument.model_dump(exclude_none=True),
            "changedFields": parsed.changedFields,
        }

    async def _extract_unit(
        self, session_id: str, unit: ReportContextExtractionUnit
    ) -> list[ReportContextChunk]:
        payload = await self._generate_json(_build_extraction_input(unit, repair=False))
        try:
            parsed = ExtractionResponsePayload.model_validate(payload)
        except ValidationError:
            payload = await self._generate_json(
                _build_extraction_input(unit, repair=True)
            )
            try:
                parsed = ExtractionResponsePayload.model_validate(payload)
            except ValidationError as exc:
                raise ReportContextProviderError(
                    f"OpenAI returned an unexpected report context payload: {exc}"
                ) from exc
        extracted_at = datetime.now(UTC)
        chunks = [
            ReportContextChunk(
                id=str(uuid.uuid4()),
                sourceFileName=unit.upload.file_name,
                sourceIndex=unit.source_index,
                pageNumber=unit.page_number,
                title=chunk.title.strip(),
                category=chunk.category.strip(),
                text=chunk.text.strip(),
                extractedAt=extracted_at,
                label=chunk.title.strip(),
            )
            for chunk in parsed.chunks
            if chunk.text.strip()
        ]
        if not chunks:
            raise ReportContextProviderError(
                f"No extractable clinical chunks were returned for {unit.upload.file_name}."
            )
        return chunks

    async def _generate_json(self, content: list[dict[str, Any]]) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            response = await client.post(
                f"{self.base_url}/responses",
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": self.model,
                    "text": {"format": {"type": "json_object"}},
                    "input": [{"role": "user", "content": content}],
                },
            )
        if response.status_code >= 400:
            raise ReportContextProviderError(
                f"OpenAI report context request failed with status {response.status_code}."
            )
        payload = response.json()
        text = _extract_openai_response_text(payload)
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ReportContextProviderError(
                f"OpenAI report context response was not valid JSON: {exc}"
            ) from exc
        if not isinstance(data, dict):
            raise ReportContextProviderError(
                "OpenAI report context response must be a JSON object."
            )
        return data


def create_report_context_provider(settings: Settings) -> BaseReportContextProvider:
    provider = settings.report_context_provider.strip().lower()
    if provider == "mock":
        return MockReportContextProvider()
    if provider == "openai":
        return OpenAIReportContextProvider(settings)
    if settings.openai_api_key:
        return OpenAIReportContextProvider(settings)
    return MockReportContextProvider()


def _expand_units(
    uploads: Sequence[StoredReportContextUpload],
    max_units: int,
) -> list[ReportContextExtractionUnit]:
    units: list[ReportContextExtractionUnit] = []
    for source_index, upload in enumerate(uploads):
        if upload.mime_type == "application/pdf":
            units.extend(_expand_pdf_units(upload, source_index))
        elif upload.mime_type.startswith("image/"):
            units.append(
                ReportContextExtractionUnit(
                    upload=upload,
                    source_index=source_index,
                    unit_index=0,
                    total_units=0,
                    image_bytes=upload.data,
                )
            )
        else:
            raise ReportContextProviderError(
                f"Unsupported report context upload type: {upload.mime_type}."
            )
    if len(units) > max_units:
        raise ReportContextProviderError(
            f"Report context extraction exceeds the configured page limit ({max_units})."
        )
    total_units = len(units)
    for index, unit in enumerate(units):
        unit.unit_index = index
        unit.total_units = total_units
    return units


def _expand_pdf_units(
    upload: StoredReportContextUpload, source_index: int
) -> list[ReportContextExtractionUnit]:
    document = fitz.open(stream=upload.data, filetype="pdf")
    try:
        units: list[ReportContextExtractionUnit] = []
        for page_index, page in enumerate(document, start=1):
            pix = page.get_pixmap(matrix=fitz.Matrix(2, 2), alpha=False)
            units.append(
                ReportContextExtractionUnit(
                    upload=upload,
                    source_index=source_index,
                    unit_index=0,
                    total_units=0,
                    page_number=page_index,
                    image_bytes=pix.tobytes("png"),
                    extracted_text=(page.get_text("text") or "").strip(),
                )
            )
        if units:
            return units
    finally:
        document.close()
    raise ReportContextProviderError(
        f"Unable to expand PDF pages for {upload.file_name}."
    )


def _build_extraction_input(
    unit: ReportContextExtractionUnit, repair: bool
) -> list[dict[str, Any]]:
    prompt_lines = [
        EXTRACTION_REPAIR_PROMPT if repair else EXTRACTION_PROMPT,
        f"Source file name: {unit.upload.file_name}",
    ]
    if unit.page_number is not None:
        prompt_lines.append(f"Page number: {unit.page_number}")
    if unit.extracted_text:
        prompt_lines.extend(
            [
                "Embedded page text extracted from the source. Use it where reliable and cross-check against the image:",
                unit.extracted_text[:12000],
            ]
        )
    content: list[dict[str, Any]] = [
        {"type": "input_text", "text": "\n\n".join(prompt_lines)}
    ]
    if unit.image_bytes:
        content.append(
            {
                "type": "input_image",
                "image_url": f"data:image/png;base64,{base64.b64encode(unit.image_bytes).decode('ascii')}",
            }
        )
    return content


def _extract_openai_response_text(payload: dict[str, Any]) -> str:
    output = payload.get("output")
    if isinstance(output, list):
        for item in output:
            if not isinstance(item, dict):
                continue
            content = item.get("content")
            if not isinstance(content, list):
                continue
            for part in content:
                if isinstance(part, dict) and isinstance(part.get("text"), str):
                    return part["text"]
    output_text = payload.get("output_text")
    if isinstance(output_text, str):
        return output_text
    raise ReportContextProviderError("OpenAI returned no report context response text.")


def _dedupe_chunks(chunks: Sequence[ReportContextChunk]) -> list[ReportContextChunk]:
    deduped: list[ReportContextChunk] = []
    seen: set[tuple[str, int | None, str, str, str]] = set()
    for chunk in chunks:
        key = (
            chunk.sourceFileName,
            chunk.pageNumber,
            chunk.title.strip().lower(),
            chunk.category.strip().lower(),
            chunk.text.strip().lower(),
        )
        if key in seen:
            continue
        seen.add(key)
        deduped.append(chunk)
    return deduped


async def _maybe_await(value: Any) -> Any:
    if hasattr(value, "__await__"):
        return await value
    return value
