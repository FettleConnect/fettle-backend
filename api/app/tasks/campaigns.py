from __future__ import annotations

from typing import Any

from app.celery_app import celery_app
from app.db import SessionLocal
from app.models import Campaign, PatientModel
from app.tasks.outbound import call_outbound_task


@celery_app.task(name="run_campaign_task")
def run_campaign_task(campaign_id: str) -> dict[str, Any]:
    session = SessionLocal()
    try:
        campaign = session.get(Campaign, campaign_id)
        if campaign is None:
            return {
                "campaign_id": campaign_id,
                "queued": 0,
                "status": "campaign_not_found",
            }

        patient_ids = list(campaign.patient_ids or [])
        queued = 0
        for patient_id in patient_ids:
            patient = session.get(PatientModel, patient_id)
            payload = {
                "campaign_id": str(campaign.id),
                "patient_id": str(patient_id),
                "template_type": campaign.template_type,
                "purpose": campaign.purpose,
            }
            if patient is not None:
                payload.update(
                    {
                        "phone_number": patient.mobile_no,
                        "patient_name": patient.patient_name,
                    }
                )
            call_outbound_task.apply_async(args=[payload])
            queued += 1

        return {
            "campaign_id": campaign_id,
            "patient_ids": patient_ids,
            "queued": queued,
            "status": "queued",
        }
    finally:
        session.close()
