from fastapi import APIRouter

from bisheng.datasource.api.endpoints.test_connection import router as test_connection_router

router = APIRouter()
router.include_router(test_connection_router)

__all__ = ["router"]
