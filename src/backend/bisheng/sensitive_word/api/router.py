from fastapi import APIRouter

from bisheng.sensitive_word.api.endpoints.hit_logs import router as hit_logs_router
from bisheng.sensitive_word.api.endpoints.platform_policy import router as platform_policy_router
from bisheng.sensitive_word.api.endpoints.platform_words import router as platform_words_router
from bisheng.sensitive_word.api.endpoints.policies import router as policies_router

router = APIRouter()
router.include_router(policies_router)
router.include_router(hit_logs_router)
# F059: the platform-wide library and its policy. Both are super-admin only —
# see `common/dependencies/super_admin.py` for why `get_tenant_admin_user`, the
# dependency the tenant policy endpoints use, is the wrong guard here.
router.include_router(platform_words_router)
router.include_router(platform_policy_router)

__all__ = ['router']
