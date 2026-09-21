from fastapi import APIRouter

from bisheng.term_library.api.endpoints.terms import router as terms_router

router = APIRouter()
router.include_router(terms_router)

__all__ = ["router"]
