#!/usr/bin/env python3
"""
Data Porting Script: Legacy Django Hospital DB → New FastAPI multi-tenant schema.

Usage:
  python scripts/port_legacy_data.py \\
      --legacy-dsn postgresql+psycopg://user:pass@host:5432/legacy_hospital \\
      --target-dsn postgresql+psycopg://user:pass@localhost:5432/fettle_dev \\
      [--dry-run]

The script is idempotent: running it twice will not create duplicate records.
It identifies records by their original UUID (preserved where possible).

Tables ported (in dependency order):
  Hospital_model           → organizations + hospital_configs
  Hospital_user_model      → users (role=hospital_user)
  Admin_model              → users (role=hospital_admin)
  Doctor_model             → users (role=doctor)
  Patient_model            → patients
  Patient_date_model       → patients (merged, with visit_date)
  HospitalUploadLog        → hospital_upload_logs
  CallFeedbackModel        → call_feedback
  EscalationModel          → escalations
  CommunityEngagementModel → community_engagements
  Outbound_Hospital        → outbound_calls
  Inbound_Hospital         → inbound_calls
  Campaign                 → campaigns
  MediVoiceSession         → medivoice_sessions
  MediVoiceTranscription   → medivoice_transcriptions
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
log = logging.getLogger("port_legacy")

# ── Helpers ───────────────────────────────────────────────────────────────────


def _to_uuid(val: Any) -> uuid.UUID | None:
    if val is None:
        return None
    if isinstance(val, uuid.UUID):
        return val
    try:
        return uuid.UUID(str(val))
    except (ValueError, AttributeError):
        return None


def _as_aware(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def _bcrypt_hash(password: str) -> str:
    """Hash a plaintext password using bcrypt."""
    import bcrypt

    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


# ── Main porter ───────────────────────────────────────────────────────────────


class LegacyPorter:
    def __init__(
        self,
        legacy_dsn: str,
        target_dsn: str,
        dry_run: bool = False,
    ) -> None:
        # Legacy uses sync psycopg — use psycopg_async for async
        self.legacy_dsn = legacy_dsn.replace("+psycopg", "+psycopg_async").replace(
            "postgresql://", "postgresql+psycopg_async://"
        )
        self.target_dsn = target_dsn.replace(
            "+psycopg://", "+psycopg_async://"
        ).replace("postgresql://", "postgresql+psycopg_async://")
        self.dry_run = dry_run

        self.legacy_engine = create_async_engine(self.legacy_dsn, echo=False)
        self.target_engine = create_async_engine(self.target_dsn, echo=False)

        self.legacy_session = async_sessionmaker(
            self.legacy_engine, class_=AsyncSession, expire_on_commit=False
        )
        self.target_session = async_sessionmaker(
            self.target_engine, class_=AsyncSession, expire_on_commit=False
        )

        # Maps legacy ID → new org UUID
        self.hospital_org_map: dict[str, uuid.UUID] = {}
        self.user_map: dict[str, uuid.UUID] = (
            {}
        )  # legacy user id → new user id (same if UUID preserved)

    async def run(self) -> None:
        log.info("Starting legacy data port%s", " (DRY RUN)" if self.dry_run else "")

        async with self.legacy_session() as legacy, self.target_session() as target:
            await self._port_hospitals(legacy, target)
            await self._port_users(legacy, target)
            await self._port_doctors(legacy, target)
            await self._port_patients(legacy, target)
            await self._port_upload_logs(legacy, target)
            await self._port_campaigns(legacy, target)
            await self._port_outbound_calls(legacy, target)
            await self._port_inbound_calls(legacy, target)
            await self._port_call_feedback(legacy, target)
            await self._port_escalations(legacy, target)
            await self._port_community_engagements(legacy, target)
            await self._port_medivoice(legacy, target)

            if not self.dry_run:
                await target.commit()
                log.info("✅ All records committed.")
            else:
                await target.rollback()
                log.info("✅ Dry run complete — no changes written.")

        await self.legacy_engine.dispose()
        await self.target_engine.dispose()

    # ── Hospitals ─────────────────────────────────────────────────────────────

    async def _port_hospitals(self, legacy: AsyncSession, target: AsyncSession) -> None:
        rows = (
            await legacy.execute(
                text(
                    "SELECT id, name, reception_email, pharmacy_email FROM app_hospital_model"
                )
            )
        ).fetchall()

        log.info("Porting %d hospitals", len(rows))
        for r in rows:
            org_id = _to_uuid(r.id) or uuid.uuid4()
            self.hospital_org_map[str(r.id)] = org_id

            slug = r.name.lower().replace(" ", "-").replace("/", "-")[:100]

            # Upsert Organization
            await target.execute(
                text(
                    """
                INSERT INTO organizations (id, name, slug, type, is_active, created_at, updated_at)
                VALUES (:id, :name, :slug, 'hospital', true, now(), now())
                ON CONFLICT (id) DO NOTHING
            """
                ),
                {"id": org_id, "name": r.name, "slug": slug},
            )

            # Upsert HospitalConfig
            await target.execute(
                text(
                    """
                INSERT INTO hospital_configs (id, organization_id, reception_email, pharmacy_email,
                    branding_json, created_at, updated_at)
                VALUES (:id, :org_id, :re, :pe, '{}', now(), now())
                ON CONFLICT (organization_id) DO NOTHING
            """
                ),
                {
                    "id": uuid.uuid4(),
                    "org_id": org_id,
                    "re": r.reception_email,
                    "pe": r.pharmacy_email,
                },
            )

    # ── Hospital users (staff) ────────────────────────────────────────────────

    async def _port_users(self, legacy: AsyncSession, target: AsyncSession) -> None:
        rows = (
            await legacy.execute(
                text(
                    """
            SELECT u.id, u.name, u.password_hash, u.patient_engagement,
                   u.community_egagement, u.revisit_engagement, u.escalation_engagement,
                   u.calllog_engagement, u.upload_engagement, u.pdf_engagement,
                   h.id as hospital_id
            FROM app_hospital_user_model u
            JOIN app_hospital_model h ON u.hospital_id = h.id
        """
                )
            )
        ).fetchall()

        log.info("Porting %d hospital users", len(rows))
        for r in rows:
            user_id = _to_uuid(r.id) or uuid.uuid4()
            org_id = self.hospital_org_map.get(str(r.hospital_id))
            if not org_id:
                log.warning(
                    "  Skipping user %s: unknown hospital %s", r.id, r.hospital_id
                )
                continue

            # Use name as email-safe identifier
            email = f"{r.name.lower().replace(' ', '.')}@hospital.local"
            self.user_map[str(r.id)] = user_id

            await target.execute(
                text(
                    """
                INSERT INTO users (id, organization_id, email, password_hash, name, role,
                    perm_patient_engagement, perm_community_engagement, perm_revisit_engagement,
                    perm_escalation_engagement, perm_calllog_engagement, perm_upload_engagement,
                    perm_pdf_engagement, is_active, created_at, updated_at)
                VALUES (:id, :org_id, :email, :pw, :name, 'hospital_user',
                    :pe, :ce, :re, :ee, :cl, :ue, :pdf, true, now(), now())
                ON CONFLICT (organization_id, email) DO NOTHING
            """
                ),
                {
                    "id": user_id,
                    "org_id": org_id,
                    "email": email,
                    "pw": r.password_hash,
                    "name": r.name,
                    "pe": r.patient_engagement,
                    "ce": r.community_egagement,
                    "re": r.revisit_engagement,
                    "ee": r.escalation_engagement,
                    "cl": r.calllog_engagement,
                    "ue": r.upload_engagement,
                    "pdf": r.pdf_engagement,
                },
            )

        # Admin users
        admin_rows = (
            await legacy.execute(
                text("SELECT id, email, password_hash FROM app_admin_model")
            )
        ).fetchall()

        log.info("Porting %d admin users", len(admin_rows))
        for r in admin_rows:
            # Admins are org-level; assign to first org if no specific mapping
            if not self.hospital_org_map:
                log.warning("  No orgs found; skipping admin %s", r.email)
                continue
            org_id = next(iter(self.hospital_org_map.values()))
            user_id = _to_uuid(r.id) or uuid.uuid4()
            self.user_map[str(r.id)] = user_id

            await target.execute(
                text(
                    """
                INSERT INTO users (id, organization_id, email, password_hash, name, role,
                    is_active, created_at, updated_at)
                VALUES (:id, :org_id, :email, :pw, :email, 'hospital_admin', true, now(), now())
                ON CONFLICT (organization_id, email) DO NOTHING
            """
                ),
                {
                    "id": user_id,
                    "org_id": org_id,
                    "email": r.email,
                    "pw": r.password_hash,
                },
            )

    # ── Doctors ───────────────────────────────────────────────────────────────

    async def _port_doctors(self, legacy: AsyncSession, target: AsyncSession) -> None:
        rows = (
            await legacy.execute(
                text(
                    """
            SELECT d.id, d.name, d.email, d.password_hash, d.mobile_number, d.department,
                   d.availability, d.timings, d.consultation_fee, h.id as hospital_id
            FROM app_doctor_model d
            JOIN app_hospital_model h ON d.hospital_id = h.id
        """
                )
            )
        ).fetchall()

        log.info("Porting %d doctors", len(rows))
        for r in rows:
            org_id = self.hospital_org_map.get(str(r.hospital_id))
            if not org_id:
                continue
            user_id = _to_uuid(r.id) or uuid.uuid4()
            self.user_map[str(r.id)] = user_id

            await target.execute(
                text(
                    """
                INSERT INTO users (id, organization_id, email, password_hash, name, role,
                    mobile_number, department, availability_json, timings, consultation_fee,
                    is_active, created_at, updated_at)
                VALUES (:id, :org_id, :email, :pw, :name, 'doctor',
                    :mob, :dept, :avail::jsonb, :timings, :fee, true, now(), now())
                ON CONFLICT (organization_id, email) DO NOTHING
            """
                ),
                {
                    "id": user_id,
                    "org_id": org_id,
                    "email": r.email,
                    "pw": r.password_hash,
                    "name": r.name,
                    "mob": r.mobile_number,
                    "dept": r.department,
                    "avail": str(r.availability) if r.availability else "[]",
                    "timings": r.timings,
                    "fee": r.consultation_fee,
                },
            )

    # ── Patients ──────────────────────────────────────────────────────────────

    async def _port_patients(self, legacy: AsyncSession, target: AsyncSession) -> None:
        rows = (
            await legacy.execute(
                text(
                    """
            SELECT id, hospital_id, serial_no, patient_name, age, mobile_no, department, uploaded_at
            FROM app_patient_model
        """
                )
            )
        ).fetchall()

        log.info("Porting %d patients (no-date)", len(rows))
        for r in rows:
            org_id = self.hospital_org_map.get(str(r.hospital_id))
            if not org_id:
                continue
            await target.execute(
                text(
                    """
                INSERT INTO patients (id, organization_id, serial_no, patient_name, age,
                    mobile_no, department, visit_date, uploaded_at)
                VALUES (:id, :org_id, :sno, :name, :age, :mob, :dept, NULL, :up)
                ON CONFLICT DO NOTHING
            """
                ),
                {
                    "id": _to_uuid(r.id) or uuid.uuid4(),
                    "org_id": org_id,
                    "sno": r.serial_no,
                    "name": r.patient_name,
                    "age": r.age,
                    "mob": r.mobile_no,
                    "dept": r.department,
                    "up": _as_aware(r.uploaded_at) or datetime.now(tz=timezone.utc),
                },
            )

        # Patient_date_model (with visit date)
        try:
            date_rows = (
                await legacy.execute(
                    text(
                        """
                SELECT id, hospital_id, serial_no, patient_name, age, mobile_no,
                       department, date, uploaded_at
                FROM app_patient_date_model
            """
                    )
                )
            ).fetchall()

            log.info("Porting %d dated patients", len(date_rows))
            for r in date_rows:
                org_id = self.hospital_org_map.get(str(r.hospital_id))
                if not org_id:
                    continue
                await target.execute(
                    text(
                        """
                    INSERT INTO patients (id, organization_id, serial_no, patient_name, age,
                        mobile_no, department, visit_date, uploaded_at)
                    VALUES (:id, :org_id, :sno, :name, :age, :mob, :dept, :vd, :up)
                    ON CONFLICT DO NOTHING
                """
                    ),
                    {
                        "id": _to_uuid(r.id) or uuid.uuid4(),
                        "org_id": org_id,
                        "sno": r.serial_no,
                        "name": r.patient_name,
                        "age": r.age,
                        "mob": r.mobile_no,
                        "dept": r.department,
                        "vd": r.date,
                        "up": _as_aware(r.uploaded_at) or datetime.now(tz=timezone.utc),
                    },
                )
        except Exception:
            log.warning("Patient_date_model table not found, skipping")

    # ── Upload logs ───────────────────────────────────────────────────────────

    async def _port_upload_logs(
        self, legacy: AsyncSession, target: AsyncSession
    ) -> None:
        try:
            rows = (
                await legacy.execute(
                    text(
                        """
                SELECT id, hospital_id, file_names, status, message, uploaded_at
                FROM app_hospitaluploadlog
            """
                    )
                )
            ).fetchall()
        except Exception:
            log.warning("HospitalUploadLog table not found, skipping")
            return

        log.info("Porting %d upload logs", len(rows))
        for r in rows:
            org_id = self.hospital_org_map.get(str(r.hospital_id))
            if not org_id:
                continue
            await target.execute(
                text(
                    """
                INSERT INTO hospital_upload_logs (id, organization_id, file_names, status, message, uploaded_at)
                VALUES (:id, :org_id, :fn::jsonb, :st, :msg, :up)
                ON CONFLICT DO NOTHING
            """
                ),
                {
                    "id": _to_uuid(r.id) or uuid.uuid4(),
                    "org_id": org_id,
                    "fn": str(r.file_names) if r.file_names else "[]",
                    "st": r.status,
                    "msg": r.message,
                    "up": _as_aware(r.uploaded_at) or datetime.now(tz=timezone.utc),
                },
            )

    # ── Campaigns ─────────────────────────────────────────────────────────────

    async def _port_campaigns(self, legacy: AsyncSession, target: AsyncSession) -> None:
        try:
            rows = (
                await legacy.execute(
                    text(
                        """
                SELECT id, hospital_id, name, template_type, audience_type, patient_ids,
                       package_name, facility_name, discount_details, purpose,
                       start_date, end_date, unconnected_only, status, created_at
                FROM app_campaign
            """
                    )
                )
            ).fetchall()
        except Exception:
            log.warning("Campaign table not found, skipping")
            return

        log.info("Porting %d campaigns", len(rows))
        for r in rows:
            org_id = self.hospital_org_map.get(str(r.hospital_id))
            if not org_id:
                continue
            await target.execute(
                text(
                    """
                INSERT INTO campaigns (id, organization_id, name, template_type, audience_type,
                    patient_ids, package_name, facility_name, discount_details, purpose,
                    start_date, end_date, unconnected_only, status, created_at)
                VALUES (:id, :org_id, :name, :tt, :at, :pids::jsonb, :pn, :fn, :dd, :purpose,
                    :sd, :ed, :uo, :st, :ca)
                ON CONFLICT DO NOTHING
            """
                ),
                {
                    "id": _to_uuid(r.id) or uuid.uuid4(),
                    "org_id": org_id,
                    "name": r.name,
                    "tt": r.template_type,
                    "at": r.audience_type,
                    "pids": str(r.patient_ids or []),
                    "pn": r.package_name,
                    "fn": r.facility_name,
                    "dd": r.discount_details,
                    "purpose": r.purpose,
                    "sd": r.start_date,
                    "ed": r.end_date,
                    "uo": r.unconnected_only,
                    "st": r.status or "created",
                    "ca": _as_aware(r.created_at) or datetime.now(tz=timezone.utc),
                },
            )

    # ── Outbound calls ────────────────────────────────────────────────────────

    async def _port_outbound_calls(
        self, legacy: AsyncSession, target: AsyncSession
    ) -> None:
        try:
            rows = (
                await legacy.execute(
                    text(
                        """
                SELECT o.id, o.vapi_id, o.status, o."endedReason", o.started_at, o.ended_at,
                       o.message_s3_link, o.audio_link, o.task_id, o.calling_process,
                       o.patient_id_id as patient_id, o.campaign_id as campaign_id,
                       p.hospital_id
                FROM phone_calling_outbound_hospital o
                JOIN app_patient_model p ON o.patient_id_id = p.id
            """
                    )
                )
            ).fetchall()
        except Exception:
            log.warning("Outbound_Hospital table not found, skipping")
            return

        log.info("Porting %d outbound calls", len(rows))
        for r in rows:
            org_id = self.hospital_org_map.get(str(r.hospital_id))
            if not org_id:
                continue
            await target.execute(
                text(
                    """
                INSERT INTO outbound_calls (id, organization_id, patient_id, campaign_id,
                    livekit_call_id, status, ended_reason, calling_process,
                    started_at, ended_at, message_s3_link, audio_s3_link, task_id, created_at)
                VALUES (:id, :org_id, :pid, :cid, :room, :st, :er, :cp, :sa, :ea, :ml, :al, :tid, now())
                ON CONFLICT DO NOTHING
            """
                ),
                {
                    "id": _to_uuid(r.id) or uuid.uuid4(),
                    "org_id": org_id,
                    "pid": _to_uuid(r.patient_id),
                    "cid": _to_uuid(r.campaign_id),
                    "room": r.vapi_id,
                    "st": r.status,
                    "er": getattr(r, "endedReason", None),
                    "cp": r.calling_process or "not_happened",
                    "sa": _as_aware(r.started_at),
                    "ea": _as_aware(r.ended_at),
                    "ml": r.message_s3_link,
                    "al": r.audio_link,
                    "tid": r.task_id,
                },
            )

    # ── Inbound calls ─────────────────────────────────────────────────────────

    async def _port_inbound_calls(
        self, legacy: AsyncSession, target: AsyncSession
    ) -> None:
        try:
            rows = (
                await legacy.execute(
                    text(
                        """
                SELECT id, hospital_id, vapi_id, status, "endedReason", started_at, ended_at,
                       message_s3_link, audio_link, task_id, calling_process,
                       from_phone_number, to_phone_numnber, department
                FROM phone_calling_inbound_hospital
            """
                    )
                )
            ).fetchall()
        except Exception:
            log.warning("Inbound_Hospital table not found, skipping")
            return

        log.info("Porting %d inbound calls", len(rows))
        for r in rows:
            org_id = self.hospital_org_map.get(str(r.hospital_id))
            if not org_id:
                continue
            await target.execute(
                text(
                    """
                INSERT INTO inbound_calls (id, organization_id, livekit_call_id, status,
                    ended_reason, calling_process, from_phone_number, to_phone_number,
                    department, started_at, ended_at, message_s3_link, audio_s3_link,
                    task_id, created_at)
                VALUES (:id, :org_id, :room, :st, :er, :cp, :from_n, :to_n, :dept,
                    :sa, :ea, :ml, :al, :tid, now())
                ON CONFLICT DO NOTHING
            """
                ),
                {
                    "id": _to_uuid(r.id) or uuid.uuid4(),
                    "org_id": org_id,
                    "room": r.vapi_id,
                    "st": r.status,
                    "er": getattr(r, "endedReason", None),
                    "cp": r.calling_process or "not_happened",
                    "from_n": r.from_phone_number,
                    "to_n": getattr(r, "to_phone_numnber", None),
                    "dept": r.department,
                    "sa": _as_aware(r.started_at),
                    "ea": _as_aware(r.ended_at),
                    "ml": r.message_s3_link,
                    "al": r.audio_link,
                    "tid": r.task_id,
                },
            )

    # ── Call feedback (outbound) ───────────────────────────────────────────────

    async def _port_call_feedback(
        self, legacy: AsyncSession, target: AsyncSession
    ) -> None:
        try:
            rows = (
                await legacy.execute(
                    text(
                        """
                SELECT f.id, f.patient_id, f.call_status, f.call_outcome, f.remarks,
                       f.community_added, f.revisit_encouraged, f.escalation_required,
                       f.call_duration, f.called_by, f.called_at, f.created_at,
                       p.hospital_id
                FROM app_callfeedbackmodel f
                JOIN app_patient_model p ON f.patient_id = p.id
            """
                    )
                )
            ).fetchall()
        except Exception:
            log.warning("CallFeedbackModel table not found, skipping")
            return

        log.info("Porting %d call feedback records", len(rows))
        for r in rows:
            org_id = self.hospital_org_map.get(str(r.hospital_id))
            if not org_id:
                continue
            await target.execute(
                text(
                    """
                INSERT INTO call_feedback (id, organization_id, patient_id, call_status,
                    call_outcome, remarks, community_added, revisit_encouraged,
                    escalation_required, call_duration, called_by, called_at, created_at)
                VALUES (:id, :org_id, :pid, :cs, :co, :rem, :ca, :re, :er, :dur, :cb, :calt, :crt)
                ON CONFLICT DO NOTHING
            """
                ),
                {
                    "id": _to_uuid(r.id) or uuid.uuid4(),
                    "org_id": org_id,
                    "pid": _to_uuid(r.patient_id),
                    "cs": r.call_status,
                    "co": r.call_outcome,
                    "rem": r.remarks,
                    "ca": r.community_added,
                    "re": r.revisit_encouraged,
                    "er": r.escalation_required,
                    "dur": r.call_duration,
                    "cb": r.called_by,
                    "calt": _as_aware(r.called_at),
                    "crt": _as_aware(r.created_at) or datetime.now(tz=timezone.utc),
                },
            )

    # ── Escalations ───────────────────────────────────────────────────────────

    async def _port_escalations(
        self, legacy: AsyncSession, target: AsyncSession
    ) -> None:
        try:
            rows = (
                await legacy.execute(
                    text(
                        """
                SELECT e.id, e.patient_id, e.issue_description, e.status, e.department,
                       e.escalated_at, e.resolved_at, e.resolution_notes, p.hospital_id
                FROM app_escalationmodel e
                JOIN app_patient_model p ON e.patient_id = p.id
            """
                    )
                )
            ).fetchall()
        except Exception:
            log.warning("EscalationModel table not found, skipping")
            return

        log.info("Porting %d escalations", len(rows))
        for r in rows:
            org_id = self.hospital_org_map.get(str(r.hospital_id))
            if not org_id:
                continue
            await target.execute(
                text(
                    """
                INSERT INTO escalations (id, organization_id, patient_id, issue_description,
                    status, department, escalated_at, resolved_at, resolution_notes)
                VALUES (:id, :org_id, :pid, :issue, :st, :dept, :ea, :ra, :rn)
                ON CONFLICT DO NOTHING
            """
                ),
                {
                    "id": _to_uuid(r.id) or uuid.uuid4(),
                    "org_id": org_id,
                    "pid": _to_uuid(r.patient_id),
                    "issue": r.issue_description,
                    "st": r.status or "pending",
                    "dept": r.department,
                    "ea": _as_aware(r.escalated_at) or datetime.now(tz=timezone.utc),
                    "ra": _as_aware(r.resolved_at),
                    "rn": r.resolution_notes,
                },
            )

    # ── Community engagements ─────────────────────────────────────────────────

    async def _port_community_engagements(
        self, legacy: AsyncSession, target: AsyncSession
    ) -> None:
        try:
            rows = (
                await legacy.execute(
                    text(
                        """
                SELECT c.id, c.patient_id, c.engagement_type, c.engagement_date, c.department, c.created_at,
                       p.hospital_id
                FROM app_communityengagementmodel c
                JOIN app_patient_model p ON c.patient_id = p.id
            """
                    )
                )
            ).fetchall()
        except Exception:
            log.warning("CommunityEngagementModel table not found, skipping")
            return

        log.info("Porting %d community engagements", len(rows))
        for r in rows:
            org_id = self.hospital_org_map.get(str(r.hospital_id))
            if not org_id:
                continue
            await target.execute(
                text(
                    """
                INSERT INTO community_engagements (id, organization_id, patient_id,
                    engagement_type, engagement_date, department, created_at)
                VALUES (:id, :org_id, :pid, :et, :ed, :dept, :crt)
                ON CONFLICT DO NOTHING
            """
                ),
                {
                    "id": _to_uuid(r.id) or uuid.uuid4(),
                    "org_id": org_id,
                    "pid": _to_uuid(r.patient_id),
                    "et": r.engagement_type,
                    "ed": _as_aware(r.engagement_date) or datetime.now(tz=timezone.utc),
                    "dept": r.department,
                    "crt": _as_aware(r.created_at) or datetime.now(tz=timezone.utc),
                },
            )

    # ── MediVoice ─────────────────────────────────────────────────────────────

    async def _port_medivoice(self, legacy: AsyncSession, target: AsyncSession) -> None:
        try:
            sessions = (
                await legacy.execute(
                    text(
                        """
                SELECT s.id, s.doctor_id, s.patient_name, s.patient_mobile, s.patient_email,
                       s.overall_summary, s.diagnosis, s.medicines, s.revisit_date,
                       s.revisit_time, s.meta_data, s.created_at, d.hospital_id
                FROM app_medivoicesession s
                JOIN app_doctor_model d ON s.doctor_id = d.id
            """
                    )
                )
            ).fetchall()
        except Exception:
            log.warning("MediVoiceSession table not found, skipping")
            return

        log.info("Porting %d MediVoice sessions", len(sessions))
        for r in sessions:
            org_id = self.hospital_org_map.get(str(r.hospital_id))
            new_doctor_id = self.user_map.get(str(r.doctor_id))
            if not org_id or not new_doctor_id:
                continue

            session_id = _to_uuid(r.id) or uuid.uuid4()
            await target.execute(
                text(
                    """
                INSERT INTO medivoice_sessions (id, organization_id, doctor_id, patient_name,
                    patient_mobile, patient_email, overall_summary, diagnosis, medicines,
                    revisit_date, revisit_time, meta_data, created_at)
                VALUES (:id, :org_id, :doc, :pn, :pm, :pe, :os, :dx, :med::jsonb,
                    :rd, :rt, :meta::jsonb, :ca)
                ON CONFLICT DO NOTHING
            """
                ),
                {
                    "id": session_id,
                    "org_id": org_id,
                    "doc": new_doctor_id,
                    "pn": r.patient_name,
                    "pm": r.patient_mobile,
                    "pe": r.patient_email,
                    "os": r.overall_summary,
                    "dx": r.diagnosis,
                    "med": str(r.medicines or []),
                    "rd": r.revisit_date,
                    "rt": r.revisit_time,
                    "meta": str(r.meta_data or {}),
                    "ca": _as_aware(r.created_at) or datetime.now(tz=timezone.utc),
                },
            )

        # Transcriptions
        try:
            trans = (
                await legacy.execute(
                    text(
                        """
                SELECT t.id, t.session_id, t.speaker, t.text, t.timestamp, t.created_at,
                       s.doctor_id, d.hospital_id
                FROM app_medivoicetranscription t
                JOIN app_medivoicesession s ON t.session_id = s.id
                JOIN app_doctor_model d ON s.doctor_id = d.id
            """
                    )
                )
            ).fetchall()

            log.info("Porting %d MediVoice transcriptions", len(trans))
            for r in trans:
                org_id = self.hospital_org_map.get(str(r.hospital_id))
                if not org_id:
                    continue
                await target.execute(
                    text(
                        """
                    INSERT INTO medivoice_transcriptions (id, organization_id, session_id,
                        speaker, text, timestamp, created_at)
                    VALUES (:id, :org_id, :sid, :sp, :txt, :ts, :ca)
                    ON CONFLICT DO NOTHING
                """
                    ),
                    {
                        "id": _to_uuid(r.id) or uuid.uuid4(),
                        "org_id": org_id,
                        "sid": _to_uuid(r.session_id),
                        "sp": r.speaker,
                        "txt": r.text,
                        "ts": r.timestamp,
                        "ca": _as_aware(r.created_at) or datetime.now(tz=timezone.utc),
                    },
                )
        except Exception as exc:
            log.warning("MediVoiceTranscription port failed: %s", exc)


# ── CLI ───────────────────────────────────────────────────────────────────────


async def main() -> None:
    parser = argparse.ArgumentParser(
        description="Port legacy Django hospital data to FastAPI schema"
    )
    parser.add_argument(
        "--legacy-dsn", required=True, help="Legacy Django DB connection string"
    )
    parser.add_argument(
        "--target-dsn", required=True, help="New FastAPI DB connection string"
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="Run without writing (rollback at end)"
    )
    args = parser.parse_args()

    porter = LegacyPorter(
        legacy_dsn=args.legacy_dsn,
        target_dsn=args.target_dsn,
        dry_run=args.dry_run,
    )
    await porter.run()


if __name__ == "__main__":
    asyncio.run(main())
