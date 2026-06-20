from app.tasks.inbound import inbound_call_task, process_inbound_calls
from fastapi import APIRouter

from .common import register_json_response

router = APIRouter(tags=["inbound"])

register_json_response(router, "/inbound/", "Inboundcall", tags=["inbound"])
register_json_response(router, "/show_inbound/", "showInboundcall", tags=["inbound"])
register_json_response(
    router, "/patientengagement_inbound/", "Patientengagement_inbound", tags=["inbound"]
)
register_json_response(
    router, "/callfeedback_inbound/", "CallFeedbackView_inbound", tags=["inbound"]
)
register_json_response(
    router,
    "/communityfeedback_inbound/",
    "CommunityfeedbackView_inbound",
    tags=["inbound"],
)
register_json_response(
    router,
    "/communityengagement_inbound/",
    "CommunityEngagement_inbound",
    tags=["inbound"],
)
register_json_response(
    router,
    "/escalationfeedback_inbound/",
    "EscalationfeedbackView_inbound",
    tags=["inbound"],
)
register_json_response(
    router,
    "/escalationengagement_inbound/",
    "EscalationEngagement_inbound",
    tags=["inbound"],
)
register_json_response(
    router,
    "/revisitengagement_inbound/",
    "RevisitAnalyticsAPIView_inbound",
    tags=["inbound"],
)
register_json_response(
    router, "/kpisummary_inbound/", "KPISummary_inbound", tags=["inbound"]
)
register_json_response(
    router, "/update_escalation_inbound/", "UpdateEscalation_inbound", tags=["inbound"]
)
register_json_response(
    router, "/process_inbound_call/", "processinboundcall_view", tags=["inbound"]
)


@router.post("/inbound/", name="Inboundcall_post", tags=["inbound"])
def create_inbound_call(payload: dict) -> dict:
    task = inbound_call_task.apply_async(args=[payload])
    return {"task_id": task.id, "status": "queued"}


@router.post(
    "/process_inbound_call/", name="processinboundcall_view_post", tags=["inbound"]
)
def process_inbound_call(payload: dict) -> dict:
    task = process_inbound_calls.apply_async(args=[payload])
    return {"task_id": task.id, "status": "queued"}
