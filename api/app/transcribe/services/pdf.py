from __future__ import annotations

import re
import uuid
from datetime import UTC, datetime

from api.app.transcribe.schemas.common import ConsultationSession, DocumentArtifact, DocumentArtifactDelivery
from api.app.transcribe.schemas.sessions import DraftConsultationDocument


def _escape_pdf_text(value: str) -> str:
    return value.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def build_minimal_pdf(lines: list[str]) -> bytes:
    content_lines = ["BT", "/F1 12 Tf", "50 780 Td"]
    first = True
    for line in lines:
        escaped = _escape_pdf_text(line)
        if not first:
            content_lines.append("0 -18 Td")
        content_lines.append(f"({escaped}) Tj")
        first = False
    content_lines.append("ET")
    stream = "\n".join(content_lines).encode("utf-8")

    objects: list[bytes] = []
    objects.append(b"1 0 obj << /Type /Catalog /Pages 2 0 R >> endobj\n")
    objects.append(b"2 0 obj << /Type /Pages /Kids [3 0 R] /Count 1 >> endobj\n")
    objects.append(
        b"3 0 obj << /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >> endobj\n"
    )
    objects.append(b"4 0 obj << /Type /Font /Subtype /Type1 /BaseFont /Helvetica >> endobj\n")
    objects.append(f"5 0 obj << /Length {len(stream)} >> stream\n".encode() + stream + b"\nendstream endobj\n")

    pdf = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for obj in objects:
        offsets.append(len(pdf))
        pdf.extend(obj)
    xref_offset = len(pdf)
    pdf.extend(f"xref\n0 {len(objects) + 1}\n".encode())
    pdf.extend(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        pdf.extend(f"{offset:010d} 00000 n \n".encode())
    pdf.extend(f"trailer << /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref_offset}\n%%EOF\n".encode())
    return bytes(pdf)


def _slugify(value: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "-", value.strip().lower())
    return normalized.strip("-") or "consultation"


def build_document_artifact(session: ConsultationSession, draft: DraftConsultationDocument) -> tuple[DocumentArtifact, bytes]:
    created_at = datetime.now(UTC)
    file_name = f"{_slugify(session.patient.name)}-{created_at.strftime('%Y%m%d-%H%M%S')}.pdf"
    lines = [
        draft.title or "Consultation Summary",
        f"Patient: {session.patient.name}",
        f"Phone: {session.patient.phoneNumber}",
        "",
        f"Clinical Summary: {draft.clinicalSummary or 'Not recorded'}",
    ]
    pdf = build_minimal_pdf(lines)
    artifact = DocumentArtifact(
        id=str(uuid.uuid4()),
        fileName=file_name,
        mimeType="application/pdf",
        sizeBytes=len(pdf),
        createdAt=created_at,
        delivery=DocumentArtifactDelivery(
            shareTitle="Consultation Summary",
            shareMessage=f"Share {file_name} with {session.patient.name} after review.",
            whatsappMessage=f"Please find your consultation summary attached for {session.patient.name}.",
        ),
    )
    return artifact, pdf


def build_transcript_pdf(session: ConsultationSession) -> bytes:
    transcription = session.transcription
    lines = [
        f"Transcript for {session.patient.name}",
        transcription.summary if transcription and transcription.summary else "Transcript summary unavailable.",
    ]
    if transcription:
        for segment in transcription.transcriptSegments:
            lines.append(f"{segment.speakerLabel}: {segment.text}")
    return build_minimal_pdf(lines)
