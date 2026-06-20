from fastapi import APIRouter

from .artifacts import router as artifacts_router
from .auth import router as auth_router
from .chatbot import router as chatbot_router
from .core import router as core_router
from .hospital import router as hospital_router
from .inbound import router as inbound_router
from .livekit import router as livekit_router
from .medivoice import router as medivoice_router
from .outbound import router as outbound_router

router = APIRouter(prefix="/api")
router.include_router(auth_router)
router.include_router(chatbot_router)
router.include_router(core_router)
router.include_router(hospital_router)
router.include_router(inbound_router)
router.include_router(livekit_router)
router.include_router(medivoice_router)
router.include_router(outbound_router)
router.include_router(artifacts_router)
