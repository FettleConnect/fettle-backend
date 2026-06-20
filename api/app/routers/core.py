from fastapi import APIRouter

from .common import register_json_response

router = APIRouter(tags=["core"])

register_json_response(router, "/admin/", "DjangoAdminSite", tags=["core"])
register_json_response(router, "/files_insert/", "patient_insert_view", tags=["core"])
register_json_response(router, "/callfeedback/", "CallFeedbackView", tags=["core"])
register_json_response(
    router, "/escalationfeedback/", "EscalationfeedbackView", tags=["core"]
)
register_json_response(router, "/update_escalation/", "UpdateEscalation", tags=["core"])
register_json_response(
    router, "/communityfeedback/", "CommunityfeedbackView", tags=["core"]
)
register_json_response(router, "/updated_community/", "UpdateCommunity", tags=["core"])
register_json_response(
    router, "/escalation_management/", "EscalationManagementView", tags=["core"]
)
register_json_response(router, "/fetchpatients/", "fetchpatients", tags=["core"])
register_json_response(
    router, "/fetchrecentactivity/", "fetchrecentactivity", tags=["core"]
)
register_json_response(router, "/admindashboard/", "AdminDashboardView", tags=["core"])
register_json_response(router, "/kpisummary/", "KPISummary", tags=["core"])
register_json_response(
    router, "/patientengagement/", "Patientengagement", tags=["core"]
)
register_json_response(
    router, "/communityengagement/", "CommunityEngagement", tags=["core"]
)
register_json_response(
    router, "/escalationengagement/", "EscalationEngagement", tags=["core"]
)
register_json_response(router, "/files_log/", "upload_files_log", tags=["core"])
register_json_response(router, "/allhospitals/", "Allhospitals", tags=["core"])
register_json_response(
    router, "/patient_call_history/", "PatientCallHistory", tags=["core"]
)
register_json_response(
    router, "/patient_lookup_by_phone/", "PatientPhoneLookupView", tags=["core"]
)
register_json_response(router, "/system_logs/", "SystemLogsView", tags=["core"])
register_json_response(
    router, "/revisitengagement/", "RevisitAnalyticsAPIView", tags=["core"]
)
register_json_response(router, "/text/", "TextView", tags=["core"])
register_json_response(router, "/tab_access/", "tab_access", tags=["core"])
register_json_response(router, "/pdf_view/", "PdfView", tags=["core"])
register_json_response(router, "/campaigns/", "CampaignView", tags=["core"])
register_json_response(
    router, "/doctor_management/", "DoctorManagementView", tags=["core"]
)
register_json_response(router, "/roi_metrics/", "ROIMetrics", tags=["core"])
register_json_response(
    router, "/department_analytics/", "DepartmentAnalytics", tags=["core"]
)
