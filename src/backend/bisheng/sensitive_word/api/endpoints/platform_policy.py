from typing import Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from bisheng.common.dependencies.super_admin import require_global_super_admin
from bisheng.common.dependencies.user_deps import UserPayload
from bisheng.common.schemas.api import resp_200
from bisheng.sensitive_word.domain.models.platform_policy import DEFAULT_AUTO_REPLY
from bisheng.sensitive_word.domain.services.platform_word_service import PlatformWordService

router = APIRouter(prefix='/platform-sensitive-word-policy', tags=['platform-sensitive-word-policy'])


class PlatformPolicyPayload(BaseModel):
    enabled: bool = False
    action: Literal['block', 'mask'] = 'mask'
    auto_reply: str = Field(default='', max_length=500)


@router.get('')
async def get_platform_policy(
    login_user: UserPayload = Depends(require_global_super_admin),
):
    """Read the platform-wide policy (super admin only, spec AC-08)."""
    policy = await PlatformWordService.aget_policy()
    return resp_200(
        {
            'enabled': bool(policy.enabled),
            'action': policy.action,
            # An unset reply resolves to the requirement's wording so the page
            # shows what users actually receive instead of an empty box. It is a
            # DISPLAY default, not a write: saving is what persists it.
            'auto_reply': policy.auto_reply or DEFAULT_AUTO_REPLY,
            'auto_reply_is_default': not bool(policy.auto_reply),
        }
    )


@router.put('')
async def update_platform_policy(
    *,
    payload: PlatformPolicyPayload,
    login_user: UserPayload = Depends(require_global_super_admin),
):
    """Write the platform-wide policy (spec AC-01/AC-09).

    Only the platform admin can reach this — tenants cannot change or disable the
    layer at all (spec AC-08), which is why there is no tenant-side counterpart.
    """
    policy = await PlatformWordService.aupsert_policy(
        enabled=payload.enabled,
        action=payload.action,
        auto_reply=payload.auto_reply.strip(),
        operator_id=login_user.user_id,
    )
    return resp_200(
        {
            'enabled': bool(policy.enabled),
            'action': policy.action,
            'auto_reply': policy.auto_reply or DEFAULT_AUTO_REPLY,
            'auto_reply_is_default': not bool(policy.auto_reply),
        }
    )
