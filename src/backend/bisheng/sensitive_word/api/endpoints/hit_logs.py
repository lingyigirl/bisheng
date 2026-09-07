from datetime import datetime

from fastapi import APIRouter, Depends, Query

from bisheng.common.dependencies.user_deps import UserPayload
from bisheng.common.schemas.api import resp_200
from bisheng.sensitive_word.domain.services.chat_guard import ChatGuardService

router = APIRouter(prefix='/sensitive-word-hit-logs', tags=['sensitive-word-hit-logs'])


@router.get('')
async def list_sensitive_word_hit_logs(
    *,
    direction: str | None = Query(None, description='input/output'),
    flow_id: str | None = Query(None, description='应用ID'),
    start_time: datetime | None = Query(None, description='开始时间(含)'),
    end_time: datetime | None = Query(None, description='结束时间(含)'),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    login_user: UserPayload = Depends(UserPayload.get_tenant_admin_user),
):
    """Tenant-scoped guardrail hit log list (spec AC-15, admin only)."""
    result = await ChatGuardService.alist_hit_logs(
        login_user,
        direction=direction,
        flow_id=flow_id,
        start_time=start_time,
        end_time=end_time,
        page=page,
        page_size=page_size,
    )
    return resp_200(result.model_dump())
