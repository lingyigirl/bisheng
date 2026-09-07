from fastapi import APIRouter

from bisheng.sensitive_word.api.endpoints.hit_logs import router as hit_logs_router
from bisheng.sensitive_word.api.endpoints.policies import router as policies_router

router = APIRouter()
router.include_router(policies_router)
router.include_router(hit_logs_router)

__all__ = ['router']
