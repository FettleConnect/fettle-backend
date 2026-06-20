from fastapi import APIRouter

from .common import register_json_response

router = APIRouter(tags=["chatbot"])

register_json_response(router, "/chat_view/", "ChatView", tags=["chatbot"])
register_json_response(router, "/chat_history/", "ChatView", tags=["chatbot"])
register_json_response(
    router, "/consultation_list/", "ConsultationListView", tags=["chatbot"]
)
register_json_response(
    router, "/archive_consultation/", "ArchiveConsultationView", tags=["chatbot"]
)
register_json_response(
    router, "/doctor_chat_view/", "DoctorChatAPIView", tags=["chatbot"]
)
register_json_response(
    router, "/doctor_send_response/", "DoctorSendResponseView", tags=["chatbot"]
)
