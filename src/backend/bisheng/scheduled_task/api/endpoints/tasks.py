"""F077 定时任务管理面（`/api/v1/scheduled-tasks`）。

端点只做三件事：解析入参、调服务层、包响应。**权限、校验、写库全在
`task_service`**——每个动作独立校验（AC-25），这里不重复。

立即运行返回 `run_trace_id`：调用方（平台面板）可据此在监控里核对本次运行；
外部系统走的是 `/api/v2/schedule/trigger`（另一条面，见 `trigger.py`）。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from bisheng.common.dependencies.user_deps import UserPayload
from bisheng.common.schemas.api import PageData, resp_200
from bisheng.scheduled_task.domain.schemas.task import (
    AvailableIdentitiesResp,
    CronPreviewReq,
    CronPreviewResp,
    NlDraftReq,
    NlDraftResp,
    ScheduledTaskCreateReq,
    ScheduledTaskItem,
    ScheduledTaskUpdateReq,
)
from bisheng.scheduled_task.domain.services import nl_draft_service, task_service

router = APIRouter(prefix="/scheduled-tasks", tags=["scheduled-task"])


def _to_item(row) -> ScheduledTaskItem:
    """任务行 → 列表项（只暴露 design §4.2 约定的对外字段）。"""
    schedule_description = ""
    schedule_display = None
    if row.schedule_type == "cron" and row.cron_expression:
        try:
            from bisheng.scheduled_task.domain.services.cron_schedule import describe, describe_parts

            schedule_description = describe(row.cron_expression)
            # AC-59：结构化描述供前端按当前语言渲染；description 保留为兼容/兜底。
            schedule_display = describe_parts(row.cron_expression)
        except Exception:  # 历史脏数据：不在列表渲染上炸
            schedule_description = row.cron_expression
    elif row.schedule_type == "once" and row.run_at is not None:
        # 一次性任务同样走结构化渲染（本地化时间格式由前端负责）。
        schedule_display = {"day": None, "time": {"kind": "once", "run_at": row.run_at.isoformat()}}
    return ScheduledTaskItem(
        id=row.id,
        name=row.name,
        description=row.description,
        app_id=row.app_id,
        app_type=row.app_type,
        schedule_type=row.schedule_type,
        cron_expression=row.cron_expression,
        run_at=row.run_at,
        schedule_description=schedule_description,
        schedule_display=schedule_display,
        status=row.status,
        next_run_at=row.next_run_at,
        last_status=row.last_status,
        last_run_at=row.last_run_at,
        last_finish_at=row.last_finish_at,
        last_duration_ms=row.last_duration_ms,
        last_error=row.last_error,
        last_trace_id=row.last_trace_id,
        last_app_version_id=row.last_app_version_id,
        blocked_reason=row.blocked_reason,
        consecutive_fail_count=row.consecutive_fail_count,
        service_account_id=row.service_account_id,
        run_identity_user_id=row.run_identity_user_id,
        created_by=row.created_by,
        created_at=row.create_time,
    )


@router.get("")
async def list_tasks(
    app_id: str | None = Query(default=None),
    app_type: int | None = Query(default=None),
    status: str | None = Query(default=None),
    keyword: str | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    user: UserPayload = Depends(UserPayload.get_login_user),
):
    rows, total = await task_service.list_tasks(
        user,
        app_id=app_id,
        app_type=app_type,
        status=status,
        keyword=keyword,
        page=page,
        page_size=page_size,
    )
    return resp_200(PageData[ScheduledTaskItem](data=[_to_item(row) for row in rows], total=total))


@router.post("")
async def create_task(
    req: ScheduledTaskCreateReq,
    user: UserPayload = Depends(UserPayload.get_login_user),
):
    task = await task_service.create_task(user, req)
    return resp_200(_to_item(task))


@router.post("/preview")
async def preview_schedule(
    req: CronPreviewReq,
    user: UserPayload = Depends(UserPayload.get_login_user),
):
    """cron 预览（AC-07）：服务端解析/归一化/人类可读/未来 N 次，前端只渲染。"""
    expression, description, display, runs = task_service.preview_schedule(req.expression)
    return resp_200(
        CronPreviewResp(expression=expression, description=description, schedule_display=display, next_runs=runs)
    )


@router.post("/nl-draft")
async def nl_draft(
    req: NlDraftReq,
    user: UserPayload = Depends(UserPayload.get_login_user),
):
    """自然语言 → 草稿（AC-41/AC-42）：**不落库**，确认后由 create 落库。

    与创建同样要求目标应用的 `edit_app`（草稿会把用户引向一次创建）。
    """
    await task_service.assert_can_manage(user, req.app_type, req.app_id)
    draft = await nl_draft_service.draft_from_text(tenant_id=user.tenant_id, user_id=user.user_id, text=req.text)
    return resp_200(NlDraftResp(**draft))


@router.get("/available-identities")
async def available_identities(
    app_id: str = Query(...),
    app_type: int = Query(...),
    user: UserPayload = Depends(UserPayload.get_login_user),
):
    """「可用运行身份」只读清单（AC-24）：无任何密钥/凭据信息。"""
    items = await task_service.list_available_identities(user, app_id=app_id, app_type=app_type)
    return resp_200(AvailableIdentitiesResp(data=items))


@router.put("/{task_id}")
async def update_task(
    task_id: int,
    req: ScheduledTaskUpdateReq,
    user: UserPayload = Depends(UserPayload.get_login_user),
):
    task = await task_service.update_task(user, task_id, req)
    return resp_200(_to_item(task))


@router.post("/{task_id}/enable")
async def enable_task(task_id: int, user: UserPayload = Depends(UserPayload.get_login_user)):
    task = await task_service.set_enabled(user, task_id, enabled=True)
    return resp_200(_to_item(task))


@router.post("/{task_id}/disable")
async def disable_task(task_id: int, user: UserPayload = Depends(UserPayload.get_login_user)):
    task = await task_service.set_enabled(user, task_id, enabled=False)
    return resp_200(_to_item(task))


@router.post("/{task_id}/run")
async def run_task_now(task_id: int, user: UserPayload = Depends(UserPayload.get_login_user)):
    """立即运行（AC-17）：同步返回本次运行的 trace id，不等运行完成。"""
    trace_id = await task_service.run_task_now(user, task_id)
    return resp_200({"run_trace_id": trace_id})


@router.delete("/{task_id}")
async def delete_task(task_id: int, user: UserPayload = Depends(UserPayload.get_login_user)):
    await task_service.delete_task(user, task_id)
    return resp_200({"deleted": True})
