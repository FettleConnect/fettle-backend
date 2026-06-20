from app.tasks.outbound import call_outbound_task, process_outbound_calls
from fastapi import APIRouter

from .common import register_json_response

router = APIRouter(tags=["outbound"])

register_json_response(router, "/outbound_call/", "Outbound_call", tags=["outbound"])
register_json_response(
    router, "/process_outbound_call/", "Process_Outbound_call", tags=["outbound"]
)
register_json_response(
    router, "/webhooks/vobiz/inbound/", "VobizInboundWebhook", tags=["outbound"]
)
register_json_response(
    router, "/webhooks/vobiz/inbound", "VobizInboundWebhook", tags=["outbound"]
)
register_json_response(
    router, "/webhooks/vobiz/outbound/", "VobizOutboundWebhook", tags=["outbound"]
)
register_json_response(
    router, "/webhooks/vobiz/outbound", "VobizOutboundWebhook", tags=["outbound"]
)
register_json_response(
    router, "/webhooks/vobiz/whatsapp/", "VobizWhatsAppWebhook", tags=["outbound"]
)
register_json_response(
    router, "/webhooks/vobiz/whatsapp", "VobizWhatsAppWebhook", tags=["outbound"]
)
register_json_response(
    router, "/download_excel/", "download_excel_outbound", tags=["outbound"]
)


@router.post("/outbound_call/", name="Outbound_call_post", tags=["outbound"])
def create_outbound_call(payload: dict) -> dict:
    task = call_outbound_task.apply_async(args=[payload])
    return {"task_id": task.id, "status": "queued"}


@router.post(
    "/process_outbound_call/", name="Process_Outbound_call_post", tags=["outbound"]
)
def process_outbound_call(payload: dict) -> dict:
    task = process_outbound_calls.apply_async(args=[payload])
    return {"task_id": task.id, "status": "queued"}


@router.post(
    "/webhooks/vobiz/outbound/", name="VobizOutboundWebhook_post", tags=["outbound"]
)
@router.post(
    "/webhooks/vobiz/outbound",
    name="VobizOutboundWebhook_post_no_slash",
    tags=["outbound"],
)
def vobiz_outbound_webhook(payload: dict) -> dict:
    task = process_outbound_calls.apply_async(args=[payload])
    return {"task_id": task.id, "status": "queued"}


@router.post(
    "/webhooks/vobiz/inbound/", name="VobizInboundWebhook_post", tags=["outbound"]
)
@router.post(
    "/webhooks/vobiz/inbound",
    name="VobizInboundWebhook_post_no_slash",
    tags=["outbound"],
)
def vobiz_inbound_webhook(payload: dict) -> dict:
    from app.tasks.inbound import inbound_call_task

    task = inbound_call_task.apply_async(args=[payload])
    return {"task_id": task.id, "status": "queued"}


@router.post(
    "/webhooks/vobiz/whatsapp/", name="VobizWhatsAppWebhook_post", tags=["outbound"]
)
@router.post(
    "/webhooks/vobiz/whatsapp",
    name="VobizWhatsAppWebhook_post_no_slash",
    tags=["outbound"],
)
def vobiz_whatsapp_webhook(payload: dict) -> dict:
    return {"payload": payload, "status": "received"}
