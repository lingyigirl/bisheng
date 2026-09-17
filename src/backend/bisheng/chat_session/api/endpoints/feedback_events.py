from datetime import datetime

from fastapi import APIRouter, Depends, Query

from bisheng.chat_session.domain.services.feedback_event_service import FeedbackEventService
from bisheng.common.dependencies.super_admin import require_global_super_admin
from bisheng.common.dependencies.user_deps import UserPayload
from bisheng.common.schemas.api import resp_200
from bisheng.core.context.tenant import get_current_tenant_id

router = APIRouter(prefix='/message-feedback-events', tags=['message-feedback-events'])


def _serialize(row: object) -> dict:
    return {
        'id': row.id,
        'tenant_id': row.tenant_id,
        'user_id': row.user_id,
        'message_id': row.message_id,
        'flow_id': row.flow_id,
        'from_value': row.from_value,
        'to_value': row.to_value,
        'comment': row.comment,
        'create_time': row.create_time.isoformat() if row.create_time else None,
    }


@router.get('')
async def list_feedback_events(
    *,
    flow_id: str | None = Query(None, description='应用ID'),
    feedback_type: int | None = Query(None, description='评价类型: 0未评/1点赞/2差评'),
    start_time: datetime | None = Query(None, description='开始时间(含)'),
    end_time: datetime | None = Query(None, description='结束时间(含)'),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    login_user: UserPayload = Depends(UserPayload.get_tenant_admin_user),
):
    """Tenant-scoped feedback trail (spec AC-25).

    The tenant id is passed EXPLICITLY rather than left to the automatic filter:
    this same DAO method also serves the super-admin path, where that filter is
    bypassed, so relying on it here would make the two callers behave
    differently in a way that is easy to miss.
    """
    tenant_id = get_current_tenant_id() or login_user.tenant_id
    rows, total = await FeedbackEventService.alist(
        tenant_id=tenant_id,
        flow_id=flow_id,
        feedback_type=feedback_type,
        start_time=start_time,
        end_time=end_time,
        page=page,
        page_size=page_size,
    )
    return resp_200({'data': [_serialize(row) for row in rows], 'total': total})


@router.get('/all')
async def list_all_feedback_events(
    *,
    tenant_id: int | None = Query(None, description='按租户筛选(可选)'),
    flow_id: str | None = Query(None, description='应用ID'),
    feedback_type: int | None = Query(None, description='评价类型: 0未评/1点赞/2差评'),
    start_time: datetime | None = Query(None, description='开始时间(含)'),
    end_time: datetime | None = Query(None, description='结束时间(含)'),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    login_user: UserPayload = Depends(require_global_super_admin),
):
    """Cross-tenant feedback trail (spec AC-26).

    Bypasses the automatic tenant filter, which would otherwise limit a platform
    admin to the root tenant and make the page look empty. `tenant_id` narrows
    the result on request.
    """
    from bisheng.core.context.tenant import bypass_tenant_filter

    with bypass_tenant_filter():
        rows, total = await FeedbackEventService.alist(
            tenant_id=tenant_id,
            flow_id=flow_id,
            feedback_type=feedback_type,
            start_time=start_time,
            end_time=end_time,
            page=page,
            page_size=page_size,
        )
    return resp_200({'data': [_serialize(row) for row in rows], 'total': total})
