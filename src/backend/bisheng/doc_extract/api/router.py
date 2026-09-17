from fastapi import APIRouter

from bisheng.doc_extract.api.endpoints.extraction import router as extraction_router
from bisheng.doc_extract.api.endpoints.records import router as records_router
from bisheng.doc_extract.api.endpoints.templates import router as templates_router

router = APIRouter()
router.include_router(templates_router)
router.include_router(extraction_router)
router.include_router(records_router)

__all__ = ['router']
