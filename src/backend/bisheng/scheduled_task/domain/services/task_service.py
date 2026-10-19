"""F077 管理面服务：任务 CRUD / 启停 / 删除 / 立即运行 / 预览 / 可用运行身份。

**权限**（design 决策 11）：六个动作各自在服务层校验，不依赖界面门控；管理员
（全局超管 / 当前租户管理员）走与 `PermissionService` 五级短路一致的显式放行
（AC-26）。列表在**不带 app 过滤**时只允许管理员，避免变成跨应用的租户级枚举面。

**创建与编辑的校验组合**（AC-20 / AC-03）：应用存在 → 编排参数合法 → 服务账号
可绑定（`trigger_guard.validate_binding`）→ 知识可见性（`knowledge_guard`，
仅开关为开的引用）→ 预设输入尺寸。任何一步失败都在**写库之前**拒绝。
"""

from __future__ import annotations

from datetime import datetime

from loguru import logger

from bisheng.common.errcode.http_error import NotFoundError, UnAuthorizedError
from bisheng.common.errcode.scheduled_task import (
    ScheduledTaskConfigInvalidError,
    ScheduledTaskIdentityUnavailableError,
    ScheduledTaskLimitExceededError,
)
from bisheng.common.services.config_service import settings
from bisheng.database.models.audit_log import AuditLogDao
from bisheng.open_api.domain.models.dao import ServiceAccountDao
from bisheng.scheduled_task.domain.constants import ScheduledTaskAuditAction
from bisheng.scheduled_task.domain.models.dao import ScheduledTaskDao
from bisheng.scheduled_task.domain.models.scheduled_task import (
    ScheduledTask,
    ScheduleType,
    TaskStatus,
)
from bisheng.scheduled_task.domain.services import knowledge_guard, run_service, trigger_guard
from bisheng.scheduled_task.domain.services.cron_schedule import (
    describe,
    next_runs,
    normalize,
    validate_and_parse,
)

TARGET_TYPE = "scheduled_task"


def _conf():
    return settings.scheduled_task


def _now() -> datetime:
    return datetime.now(run_service.SERVER_TZ).replace(tzinfo=None)


async def _is_admin(user) -> bool:
    """全局超管或当前租户管理员（与 PermissionService 五级短路同口径）。"""
    if bool(getattr(user, "is_global_super", False)):
        return True
    try:
        return await user.has_tenant_admin(user.tenant_id)
    except Exception:
        logger.warning("scheduled_task: tenant-admin check failed for user {}", getattr(user, "user_id", None))
        return False


async def assert_can_manage(user, app_type: int, app_id: str) -> None:
    """应用级 ``edit_app``（编辑者档起）；管理员显式放行（AC-25 / AC-26）。"""
    if await _is_admin(user):
        return
    object_type = trigger_guard.object_type_of(app_type)
    if object_type is None:
        raise ScheduledTaskConfigInvalidError()
    from bisheng.permission.domain.services.application_permission_service import (
        ApplicationPermissionService,
    )

    allowed = await ApplicationPermissionService.has_any_permission_async(user, object_type, str(app_id), ["edit_app"])
    if not allowed:
        raise UnAuthorizedError()


# ── 校验零件 ─────────────────────────────────────────────────────────


def _validate_schedule(
    schedule_type: str, cron_expression: str | None, run_at: datetime | None
) -> tuple[str, str | None, datetime]:
    """返回 ``(schedule_type, normalized_cron, next_run_at)``；非法即 11902/11903。"""
    if schedule_type not in (ScheduleType.CRON.value, ScheduleType.ONCE.value):
        raise ScheduledTaskConfigInvalidError(msg="schedule_type must be cron or once")

    now = _now()
    if schedule_type == ScheduleType.ONCE.value:
        if run_at is None:
            raise ScheduledTaskConfigInvalidError(msg="run_at is required for a one-off task")
        # 一次性任务的 next_run_at 即 run_at（扫描唯一依据，design §4.2）。
        return schedule_type, None, run_at

    if not cron_expression:
        raise ScheduledTaskConfigInvalidError(msg="cron_expression is required")
    validate_and_parse(cron_expression)  # 非法 → 11903
    normalized = normalize(cron_expression)
    return (
        schedule_type,
        normalized,
        next_runs(normalized, now.replace(tzinfo=run_service.SERVER_TZ), 1)[0].replace(tzinfo=None),
    )


def _validate_presets(presets) -> list[dict] | None:
    if presets is None:
        return None
    conf = _conf()
    if len(presets) > conf.preset_input_max_items:
        raise ScheduledTaskConfigInvalidError(msg="too many preset inputs")
    normalized = []
    for item in presets:
        value = item.value if hasattr(item, "value") else item.get("value", "")
        key = item.key if hasattr(item, "key") else item.get("key", "")
        if not key or len(str(value)) > conf.preset_input_max_chars:
            raise ScheduledTaskConfigInvalidError(msg="invalid preset input")
        normalized.append({"key": key, "value": value})
    return normalized


async def _validate_identity_and_knowledge(
    *, app_id: str, app_type: int, service_account_id: int, tenant_id: int
) -> int:
    """校验并返回要锁定的运行身份 user id（AC-20 + AC-29）。"""
    app_ok, binding = await trigger_guard.validate_binding(
        app_id=app_id, app_type=app_type, service_account_id=service_account_id, tenant_id=tenant_id
    )
    if not app_ok:
        raise ScheduledTaskConfigInvalidError(msg="target app not found or type mismatch")
    if not binding.ok or binding.owner_user_id is None:
        raise ScheduledTaskIdentityUnavailableError(msg=binding.identity_state.value)

    await knowledge_guard.assert_app_knowledge_readable(
        app_id=app_id, app_type=app_type, owner_user_id=binding.owner_user_id, tenant_id=tenant_id
    )
    return binding.owner_user_id


# ── 审计 ─────────────────────────────────────────────────────────────


async def _audit(user, action: ScheduledTaskAuditAction, task: ScheduledTask, **metadata) -> None:
    """管理动作审计（进审计页白名单；best-effort 不改变业务结果）。"""
    try:
        await AuditLogDao.ainsert_v2(
            tenant_id=task.tenant_id,
            operator_id=getattr(user, "user_id", 0),
            operator_tenant_id=task.tenant_id,
            action=action.value,
            target_type=TARGET_TYPE,
            target_id=str(task.id),
            metadata=metadata or None,
            object_name=task.name,
        )
    except Exception:
        logger.exception("scheduled_task: audit write failed for {} on task {}", action.value, task.id)


# ── CRUD ────────────────────────────────────────────────────────────


async def create_task(user, req) -> ScheduledTask:
    await assert_can_manage(user, req.app_type, req.app_id)

    enabled_count = await ScheduledTaskDao.acount_enabled_by_app(req.app_id, req.app_type)
    if enabled_count >= _conf().max_tasks_per_app:
        raise ScheduledTaskLimitExceededError(exception=f"current enabled tasks: {enabled_count}")

    schedule_type, cron_expression, next_run_at = _validate_schedule(req.schedule_type, req.cron_expression, req.run_at)
    presets = _validate_presets(req.preset_inputs)
    owner_user_id = await _validate_identity_and_knowledge(
        app_id=req.app_id,
        app_type=req.app_type,
        service_account_id=req.service_account_id,
        tenant_id=user.tenant_id,
    )

    task = await ScheduledTaskDao.acreate(
        ScheduledTask(
            name=req.name,
            description=req.description,
            app_id=req.app_id,
            app_type=req.app_type,
            schedule_type=schedule_type,
            cron_expression=cron_expression,
            run_at=req.run_at,
            next_run_at=next_run_at,
            service_account_id=req.service_account_id,
            run_identity_user_id=owner_user_id,
            preset_inputs=presets,
            created_by=user.user_id,
        )
    )
    await _audit(user, ScheduledTaskAuditAction.TASK_CREATE, task)
    return task


async def update_task(user, task_id: int, req) -> ScheduledTask:
    task = await ScheduledTaskDao.aget(task_id)
    if task is None:
        raise NotFoundError()
    await assert_can_manage(user, task.app_type, task.app_id)

    fields: dict = {}
    before: dict = {}

    if req.name is not None and req.name != task.name:
        fields["name"] = req.name
        before["name"] = task.name
    if req.description is not None and req.description != task.description:
        fields["description"] = req.description
        before["description"] = task.description
    if req.preset_inputs is not None:
        presets = _validate_presets(req.preset_inputs)
        fields["preset_inputs"] = presets
        before["preset_inputs"] = "changed"
    if req.service_account_id is not None and req.service_account_id != task.service_account_id:
        # 换运行身份：走与创建期相同的校验（AC-03），通过后同时更新锁定的 owner。
        owner_user_id = await _validate_identity_and_knowledge(
            app_id=task.app_id,
            app_type=task.app_type,
            service_account_id=req.service_account_id,
            tenant_id=task.tenant_id,
        )
        fields["service_account_id"] = req.service_account_id
        fields["run_identity_user_id"] = owner_user_id
        before["service_account_id"] = task.service_account_id
    if req.schedule_type is not None or req.cron_expression is not None or req.run_at is not None:
        schedule_type, cron_expression, next_run_at = _validate_schedule(
            req.schedule_type or task.schedule_type,
            req.cron_expression if req.cron_expression is not None else task.cron_expression,
            req.run_at if req.run_at is not None else task.run_at,
        )
        fields.update({"schedule_type": schedule_type, "cron_expression": cron_expression, "next_run_at": next_run_at})
        before["schedule"] = task.cron_expression or (task.run_at.isoformat() if task.run_at else None)

    if not fields:
        return task

    updated = await ScheduledTaskDao.aupdate_fields(task.id, fields, tenant_id=task.tenant_id)
    if updated != 1:
        raise NotFoundError()
    task = await ScheduledTaskDao.aget(task_id)
    await _audit(user, ScheduledTaskAuditAction.TASK_UPDATE, task, before=before, after=list(fields))
    return task


async def set_enabled(user, task_id: int, *, enabled: bool) -> ScheduledTask:
    task = await ScheduledTaskDao.aget(task_id)
    if task is None:
        raise NotFoundError()
    await assert_can_manage(user, task.app_type, task.app_id)

    fields: dict = {"status": TaskStatus.ENABLED.value if enabled else TaskStatus.DISABLED.value}
    if enabled:
        # AC-02：重新启用自下一个计划时刻起恢复——停用期间到期的轮次不补跑。
        if task.schedule_type == ScheduleType.CRON.value and task.cron_expression:
            fields["next_run_at"] = next_runs(task.cron_expression, _now().replace(tzinfo=run_service.SERVER_TZ), 1)[
                0
            ].replace(tzinfo=None)
        fields["blocked_reason"] = None

    await ScheduledTaskDao.aupdate_fields(task.id, fields, tenant_id=task.tenant_id)
    task = await ScheduledTaskDao.aget(task_id)
    action = ScheduledTaskAuditAction.TASK_ENABLE if enabled else ScheduledTaskAuditAction.TASK_DISABLE
    await _audit(user, action, task)
    return task


async def delete_task(user, task_id: int) -> None:
    task = await ScheduledTaskDao.aget(task_id)
    if task is None:
        raise NotFoundError()
    await assert_can_manage(user, task.app_type, task.app_id)
    await ScheduledTaskDao.adelete(task.id, tenant_id=task.tenant_id)
    await _audit(user, ScheduledTaskAuditAction.TASK_DELETE, task)


async def run_task_now(user, task_id: int) -> str:
    """立即运行：权限同管理动作，实际编排在 `run_service.run_now`（AC-17）。"""
    task = await ScheduledTaskDao.aget(task_id)
    if task is None:
        raise NotFoundError()
    await assert_can_manage(user, task.app_type, task.app_id)
    trace_id = await run_service.run_now(task_id)
    await _audit(user, ScheduledTaskAuditAction.TASK_RUN_NOW, task, trace_id=trace_id)
    return trace_id


# ── 列表 / 预览 / 可用身份 ────────────────────────────────────────────


async def list_tasks(
    user,
    *,
    app_id: str | None,
    app_type: int | None,
    status: str | None,
    keyword: str | None,
    page: int,
    page_size: int,
):
    if app_id:
        await assert_can_manage(user, app_type or 0, app_id)
    elif not await _is_admin(user):
        # 不带 app 过滤的列表是租户级枚举面，只给管理员（普通编辑者按应用查）。
        raise UnAuthorizedError()

    rows, total = await ScheduledTaskDao.alist(
        app_id=app_id, app_type=app_type, status=status, keyword=keyword, page=page, page_size=page_size
    )
    return rows, total


def preview_schedule(expression: str):
    """cron 预览：归一化 + 人类可读 + 未来 N 次（服务端算，AC-07）。"""
    validate_and_parse(expression)
    normalized = normalize(expression)
    anchor = _now().replace(tzinfo=run_service.SERVER_TZ)
    return normalized, describe(normalized), next_runs(normalized, anchor, _conf().preview_count)


async def list_available_identities(user, *, app_id: str, app_type: int) -> list[dict]:
    """按目标应用列出可用运行身份（AC-24）：只读、零凭据信息、区分 usable。"""
    await assert_can_manage(user, app_type, app_id)
    object_type = trigger_guard.object_type_of(app_type)
    if object_type is None:
        raise ScheduledTaskConfigInvalidError()

    from bisheng.permission.domain.services.application_permission_service import (
        ApplicationPermissionService,
    )

    accounts, _ = await ServiceAccountDao.alist(tenant_id=user.tenant_id, page=1, page_size=1000)
    items: list[dict] = []
    for account in accounts:
        if not account.is_enabled:
            items.append(
                {
                    "service_account_id": account.id,
                    "name": account.name,
                    "owner_user_id": account.resource_owner_user_id,
                    "enabled": False,
                    "has_use_app": False,
                    "usable": False,
                }
            )
            continue
        payload = await trigger_guard.load_owner_payload(
            owner_user_id=account.resource_owner_user_id, tenant_id=user.tenant_id
        )
        privileged = bool(payload) and await trigger_guard.owner_is_privileged(payload, user.tenant_id)
        has_use_app = bool(payload) and await ApplicationPermissionService.has_any_permission_async(
            payload, object_type, str(app_id), ["use_app"]
        )
        items.append(
            {
                "service_account_id": account.id,
                "name": account.name,
                "owner_user_id": account.resource_owner_user_id,
                "enabled": True,
                "has_use_app": has_use_app,
                "usable": bool(payload) and not privileged and has_use_app,
            }
        )
    return items


def assert_app_deletable(app_id: str, app_type: int) -> None:
    """删除应用前的引用拦截（AC-30）：存在启用中的定时任务则拒绝并回带数量。

    被两个**同步**删除端点调用（`api/v1/flows.py::delete_flow` /
    `api/services/assistant.py::delete_assistant`），因此本函数是同步的。
    只统计启用中的任务——停用或历史的任务不阻断删除（对齐数据源引用的"只算活引用"）。
    """
    from bisheng.common.errcode.scheduled_task import ScheduledTaskAppInUseError

    count = ScheduledTaskDao.count_enabled_by_app_sync(str(app_id), app_type)
    if count:
        raise ScheduledTaskAppInUseError(
            exception=f"enabled scheduled tasks: {count}",
            count=count,
        )
