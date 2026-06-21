from __future__ import annotations

from fastapi import APIRouter

from api.app.views import legacy_hospital as views

router = APIRouter(tags=["legacy-hospital"])

router.add_api_route("/files_insert/", views.files_insert, methods=["POST"])
router.add_api_route("/fetchpatients/", views.fetch_patients, methods=["GET"])
router.add_api_route("/files_log/", views.files_log, methods=["GET"])
router.add_api_route("/allhospitals/", views.all_hospitals, methods=["GET"])
router.add_api_route(
    "/patient_call_history/", views.patient_call_history, methods=["GET"]
)

router.add_api_route("/callfeedback/", views.call_feedback, methods=["POST"])
router.add_api_route(
    "/escalationfeedback/", views.escalation_feedback, methods=["POST"]
)
router.add_api_route("/update_escalation/", views.update_escalation, methods=["POST"])
router.add_api_route(
    "/escalation_management/", views.escalation_management, methods=["GET"]
)
router.add_api_route("/communityfeedback/", views.community_feedback, methods=["POST"])
router.add_api_route("/updated_community/", views.update_community, methods=["POST"])

router.add_api_route("/fetchrecentactivity/", views.recent_activity, methods=["GET"])
router.add_api_route("/admindashboard/", views.admin_dashboard, methods=["GET"])
router.add_api_route("/kpisummary/", views.kpi_summary, methods=["GET"])
router.add_api_route("/patientengagement/", views.patient_engagement, methods=["GET"])
router.add_api_route(
    "/communityengagement/", views.community_engagement, methods=["GET"]
)
router.add_api_route(
    "/escalationengagement/", views.escalation_engagement, methods=["GET"]
)
router.add_api_route("/revisitengagement/", views.revisit_engagement, methods=["GET"])
router.add_api_route("/roi_metrics/", views.roi_metrics, methods=["GET"])
router.add_api_route(
    "/department_analytics/", views.department_analytics, methods=["GET"]
)
# LEGACY: preserve old Django route names that predate the /api/v1 redesign.
router.add_api_route("/system_logs/", views.system_logs, methods=["GET"])
router.add_api_route("/campaigns/", views.campaigns, methods=["GET", "POST", "DELETE"])

router.add_api_route("/tab_access/", views.tab_access, methods=["GET"])
router.add_api_route("/text/", views.get_text, methods=["GET"])
router.add_api_route("/text/", views.save_text, methods=["POST"])
router.add_api_route("/pdf_view/", views.pdf_view, methods=["POST"])

router.add_api_route("/outbound_call/", views.outbound_call, methods=["POST"])
router.add_api_route(
    "/process_outbound_call/", views.process_outbound_call, methods=["POST"]
)
router.add_api_route("/download_excel/", views.download_excel, methods=["GET"])

router.add_api_route("/inbound/", views.show_inbound, methods=["GET"])
router.add_api_route("/show_inbound/", views.show_inbound, methods=["GET"])
router.add_api_route("/inbound/", views.create_inbound, methods=["POST"])
router.add_api_route(
    "/process_inbound_call/", views.process_inbound_call, methods=["POST"]
)
router.add_api_route("/kpisummary_inbound/", views.kpi_inbound, methods=["GET"])
router.add_api_route(
    "/patientengagement_inbound/", views.patient_engagement_inbound, methods=["GET"]
)
router.add_api_route(
    "/communityengagement_inbound/",
    views.community_engagement_inbound,
    methods=["GET"],
)
router.add_api_route(
    "/escalationengagement_inbound/",
    views.escalation_engagement_inbound,
    methods=["GET"],
)
router.add_api_route(
    "/revisitengagement_inbound/", views.revisit_engagement_inbound, methods=["GET"]
)
router.add_api_route("/callfeedback_inbound/", views.inbound_feedback, methods=["POST"])
router.add_api_route(
    "/communityfeedback_inbound/",
    views.inbound_community_feedback,
    methods=["POST"],
)
router.add_api_route(
    "/escalationfeedback_inbound/",
    views.inbound_escalation_feedback,
    methods=["POST"],
)
router.add_api_route(
    "/update_escalation_inbound/",
    views.update_inbound_escalation,
    methods=["POST"],
)

router.add_api_route(
    "/webhooks/vobiz/inbound", views.vobiz_inbound_webhook, methods=["POST"]
)
router.add_api_route(
    "/webhooks/vobiz/inbound/", views.vobiz_inbound_webhook, methods=["POST"]
)
router.add_api_route(
    "/webhooks/vobiz/outbound", views.vobiz_outbound_webhook, methods=["POST"]
)
router.add_api_route(
    "/webhooks/vobiz/outbound/", views.vobiz_outbound_webhook, methods=["POST"]
)
router.add_api_route(
    "/webhooks/vobiz/whatsapp", views.vobiz_whatsapp_webhook, methods=["POST"]
)
router.add_api_route(
    "/webhooks/vobiz/whatsapp/", views.vobiz_whatsapp_webhook, methods=["POST"]
)
router.add_api_route("/livekit/webhook/", views.livekit_webhook, methods=["POST"])

router.add_api_route("/staff/availability/", views.staff_availability, methods=["GET"])
router.add_api_route("/doctor_management/", views.doctor_management, methods=["GET"])
router.add_api_route(
    "/doctor_management/", views.create_or_update_doctor, methods=["POST"]
)
router.add_api_route("/doctor/login/", views.doctor_login, methods=["POST"])
router.add_api_route(
    "/hospital/doctor_transcripts/", views.doctor_transcripts, methods=["GET"]
)
router.add_api_route("/medivoice/sessions/", views.medivoice_sessions, methods=["GET"])
router.add_api_route(
    "/medivoice/sessions/", views.create_medivoice_session, methods=["POST"]
)
router.add_api_route("/medivoice/sync/", views.medivoice_sync, methods=["POST"])
router.add_api_route(
    "/medivoice/prescription/{session_id}/",
    views.medivoice_prescription,
    methods=["GET"],
)
router.add_api_route(
    "/transcription_artifacts/{session_id}/{artifact_type}/",
    views.transcription_artifacts,
    methods=["GET"],
)
