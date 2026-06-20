from fastapi import APIRouter

from .common import register_json_response

router = APIRouter(tags=["auth"])

register_json_response(router, "/auth/google/", "GoogleAuthView", tags=["auth"])
register_json_response(
    router, "/validate_token/", "DermatologyValidateTokenView", tags=["auth"]
)
register_json_response(router, "/login/", "login_view", tags=["auth"])
register_json_response(router, "/doctor/login/", "doctor_login_view", tags=["auth"])
