from fastapi import APIRouter

from bisheng.datasource.api.endpoints.datasources import router as datasources_router
from bisheng.datasource.api.endpoints.failures import router as failures_router
from bisheng.datasource.api.endpoints.test_connection import router as test_connection_router

router = APIRouter()
router.include_router(datasources_router)
router.include_router(test_connection_router)
router.include_router(failures_router)

__all__ = ["router"]
