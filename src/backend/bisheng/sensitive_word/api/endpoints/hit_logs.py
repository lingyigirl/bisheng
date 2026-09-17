from datetime import datetime

from fastapi import APIRouter, Depends, Query

from bisheng.common.dependencies.super_admin import require_global_super_admin
from bisheng.common.dependencies.user_deps import UserPayload
from bisheng.common.schemas.api import resp_200
from bisheng.sensitive_word.domain.services.chat_guard import ChatGuardService

router = APIRouter(prefix='/sensitive-word-hit-logs', tags=['sensitive-word-hit-logs'])


@router.get('')
async def list_sensitive_word_hit_logs(
    *,
    direction: str | None = Query(None, description='input/output'),
    flow_id: str | None = Query(None, description='应用ID'),
    source: str | None = Query(None, description='命中来源: tenant/platform'),
    start_time: datetime | None = Query(None, description='开始时间(含)'),
    end_time: datetime | None = Query(None, description='结束时间(含)'),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    login_user: UserPayload = Depends(UserPayload.get_tenant_admin_user),
):
    """Tenant-scoped guardrail hit log list (spec AC-15/AC-18/AC-19).

    Platform-sourced rows ARE listed — a tenant must be able to tell "the
    guardrail fired" from "the system is broken" — but their hit words are
    stripped before the response is built (`ChatGuardService.alist_hit_logs`).
    The redaction happens in the service, not here and not in the browser: a
    frontend-only hide would still ship the words over the wire.
    """
    result = await ChatGuardService.alist_hit_logs(
        login_user,
        direction=direction,
        flow_id=flow_id,
        source=source,
        start_time=start_time,
        end_time=end_time,
        page=page,
        page_size=page_size,
        redact_platform_hits=True,
    )
    return resp_200(result.model_dump())


@router.get('/all')
async def list_all_hit_logs(
    *,
    direction: str | None = Query(None, description='input/output'),
    flow_id: str | None = Query(None, description='应用ID'),
    source: str | None = Query(None, description='命中来源: tenant/platform'),
    tenant_id: int | None = Query(None, description='按租户筛选(可选)'),
    start_time: datetime | None = Query(None, description='开始时间(含)'),
    end_time: datetime | None = Query(None, description='结束时间(含)'),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    login_user: UserPayload = Depends(require_global_super_admin),
):
    """Cross-tenant hit log for the platform admin (spec AC-20).

    Hit words are NOT redacted here — the platform admin owns the library, so
    there is nothing to protect them from, and hiding the words would defeat the
    purpose of the view.

    `tenant_id=None` means "all tenants", implemented by bypassing the automatic
    tenant filter rather than by listing ids: the super admin has no single
    "current tenant" to filter by, and enumerating tenants would silently omit
    any created later in the same process lifetime.
    """
    result = await ChatGuardService.alist_hit_logs_for_super_admin(
        direction=direction,
        flow_id=flow_id,
        source=source,
        tenant_id=tenant_id,
        start_time=start_time,
        end_time=end_time,
        page=page,
        page_size=page_size,
    )
    return resp_200(result.model_dump())
