from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from api.app.transcribe.schemas.common import (
    ConsultationSession,
    DoctorProfile,
    PatientSummary,
    PublicDoctorProfile,
)


@dataclass
class DoctorRecord:
    profile: DoctorProfile
    pin: str
    token_version: int = 0
    must_change_pin: bool = False


def _now() -> datetime:
    return datetime.now(UTC)


class InMemoryDoctorRepository:
    def __init__(self) -> None:
        now = _now()
        self._doctors: dict[str, DoctorRecord] = {}
        for name, department in [
            ("Dr. Kishore B Reddy", "Orthopedics Oncology"),
            ("Dr. Santoshini Nemuri", "Pediatric Orthopedics"),
            ("Dr. Imran Ul Haq", "Cardiology"),
        ]:
            doctor_id = str(uuid.uuid4())
            self._doctors[doctor_id] = DoctorRecord(
                profile=DoctorProfile(
                    id=doctor_id,
                    name=name,
                    department=department,
                    createdAt=now,
                    lastLoginAt=now,
                ),
                pin="123456",
            )

    def list_doctors(self, search: str | None = None) -> list[PublicDoctorProfile]:
        normalized = (search or "").strip().lower()
        doctors = [record.profile for record in self._doctors.values()]
        if normalized:
            doctors = [
                doctor
                for doctor in doctors
                if normalized in doctor.name.lower()
                or normalized in (doctor.department or "").lower()
            ]
        doctors.sort(key=lambda doctor: doctor.name.lower())
        return [
            PublicDoctorProfile(
                id=doctor.id, name=doctor.name, department=doctor.department
            )
            for doctor in doctors
        ]

    def find_by_id(self, doctor_id: str) -> DoctorRecord | None:
        return self._doctors.get(doctor_id)

    def touch_last_login(self, doctor_id: str) -> DoctorRecord | None:
        doctor = self._doctors.get(doctor_id)
        if not doctor:
            return None
        doctor.profile = doctor.profile.model_copy(update={"lastLoginAt": _now()})
        return doctor


class InMemorySessionRepository:
    def __init__(self) -> None:
        self._sessions: dict[str, ConsultationSession] = {}

    def create_session(
        self,
        doctor_id: str,
        patient_name: str,
        patient_phone_number: str,
        patient_age: int | None,
    ) -> ConsultationSession:
        now = _now()
        session = ConsultationSession(
            id=str(uuid.uuid4()),
            doctorId=doctor_id,
            patient=PatientSummary(
                name=patient_name, phoneNumber=patient_phone_number, age=patient_age
            ),
            caseStatus="open",
            status="draft",
            createdAt=now,
            updatedAt=now,
        )
        self._sessions[session.id] = session
        return session

    def list_sessions(
        self, doctor_id: str, search: str | None = None
    ) -> list[ConsultationSession]:
        normalized = (search or "").strip().lower()
        sessions = [
            session
            for session in self._sessions.values()
            if session.doctorId == doctor_id
        ]
        if normalized:
            sessions = [
                session
                for session in sessions
                if normalized in session.patient.name.lower()
                or normalized in session.patient.phoneNumber
            ]
        sessions.sort(key=lambda session: session.createdAt, reverse=True)
        return sessions

    def get_session(
        self, session_id: str, doctor_id: str
    ) -> ConsultationSession | None:
        session = self._sessions.get(session_id)
        if session and session.doctorId == doctor_id:
            return session
        return None

    def get_patient_history(
        self, session_id: str, doctor_id: str
    ) -> list[ConsultationSession]:
        session = self.get_session(session_id, doctor_id)
        if not session:
            return []
        sessions = [
            candidate
            for candidate in self._sessions.values()
            if candidate.id != session.id
            and candidate.doctorId == doctor_id
            and candidate.patient.phoneNumber == session.patient.phoneNumber
            and candidate.createdAt < session.createdAt
        ]
        sessions.sort(key=lambda candidate: candidate.createdAt, reverse=True)
        return sessions[:10]

    def update_case_status(
        self, session_id: str, doctor_id: str, case_status: str
    ) -> ConsultationSession | None:
        session = self.get_session(session_id, doctor_id)
        if not session:
            return None
        updated = session.model_copy(
            update={"caseStatus": case_status, "updatedAt": _now()}
        )
        self._sessions[session_id] = updated
        return updated
