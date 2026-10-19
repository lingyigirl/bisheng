"""F077 调度编排：一轮扫描（``run_cycle``）与运行监督（``observe_runs``）。

**本模块只做编排，不做判定**：判定在 ``run_planner``（纯函数），复检在
``trigger_guard``，写回在 DAO 的原子原语。编排顺序即 design 决策 2/4：

    平台级 Redis 锁 → 租约回收 → 跨租户枚举到期 → 逐条（设租户 → 复检 →
    classify → 原子写回 → 派发）→ 运行监督（终态回写 / INPUT 处理）

**失败记账的单一写入者**：只有计划轮次（本模块）写失败状态与连续失败计数；
事件触发与立即运行由端点走 ``run_planner`` 后直接回 119 段错误、不落库。

**可测试性**：派发（``dispatch``）、运行态读取（``read_state``）、通知（``notify``）
都是可注入的；测试用桩覆盖全部分支，不碰 broker、不碰真实 Redis 队列。
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from loguru import logger

from bisheng.common.services.config_service import settings
from bisheng.core.cache.redis_manager import get_redis_client
from bisheng.core.context.tenant import bypass_tenant_filter, get_current_tenant_id, set_current_tenant_id
from bisheng.database.models.audit_log import AuditLogDao
from bisheng.database.models.flow import FlowDao, FlowType
from bisheng.scheduled_task.domain.constants import ScheduledTaskAuditAction
from bisheng.scheduled_task.domain.models.dao import ScheduledTaskDao
from bisheng.scheduled_task.domain.models.scheduled_task import (
    BlockedReason,
    RunStatus,
    ScheduledTask,
    ScheduleType,
    TaskStatus,
)
from bisheng.scheduled_task.domain.services import trigger_guard
from bisheng.scheduled_task.domain.services.cron_schedule import next_runs
from bisheng.scheduled_task.domain.services.run_planner import (
    PlanReason,
    PlanVariant,
    TaskSnapshot,
    TriggerKind,
    cap_room,
    classify,
)
from bisheng.utils import generate_uuid
from bisheng.worker.knowledge.lua_scripts import RELEASE_LOCK

SERVER_TZ = ZoneInfo("Asia/Shanghai")

#: 平台级"一轮只有一个派发者"锁（多 beat 实例共存时串行化；DB 原子 claim 兜底）。
DISPATCH_LOCK_KEY = "scheduled_task:dispatch:lock"

#: 审计与通知的稳定标识。
TARGET_TYPE = "scheduled_task"
ACTION_FAILED = "scheduled_task_failed"
ACTION_NEEDS_INPUT = "scheduled_task_needs_input"


@dataclass
class CycleStats:
    """一轮的计数（日志/指标用；字段名即 `schedule.scan.round` 的键）。"""

    lock_skipped: bool = False
    reclaimed: int = 0
    due: int = 0
    claimed: int = 0
    skipped: int = 0
    failed: int = 0
    deferred: int = 0
    dispatch_errors: int = 0
    observed: int = 0
    reasons: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "lock_skipped": self.lock_skipped,
            "reclaimed": self.reclaimed,
            "due": self.due,
            "claimed": self.claimed,
            "skipped": self.skipped,
            "failed": self.failed,
            "deferred": self.deferred,
            "dispatch_errors": self.dispatch_errors,
            "observed": self.observed,
        }


def _conf():
    return settings.scheduled_task


def _now() -> datetime:
    """调度链路的 `now`（**naive 本地时间**）。

    平台列惯例：库里的时间列是 naive 本地时间（`enable_utc=False`）。tz-aware 只在
    `cron_schedule` 的求值边界出现——把它扩散到 DB 比较或 planner 会引入
    "aware 与 naive 混比"的 TypeError / 字符串比较错序（W2 实现期实测）。
    """
    return datetime.now(SERVER_TZ).replace(tzinfo=None)


# ── 平台级锁 ─────────────────────────────────────────────────────────


async def _acquire_lock(ttl_seconds: int) -> str | None:
    """`SET NX EX` 单次往返占锁；Redis 不可用时返回 None（本轮跳过，不派发）。"""
    try:
        redis = await get_redis_client()
        token = generate_uuid()
        acquired = await redis.async_connection.set(DISPATCH_LOCK_KEY, token, nx=True, ex=ttl_seconds)
        return token if acquired else None
    except Exception:
        # 调度是后台能力：Redis 抖动时宁可本轮不跑（下一 tick 再来），也不要把
        # 异常抛给 beat（会让整个 beat 条目按 celery 重试策略堆积）。
        logger.exception("scheduled_task: could not acquire dispatch lock; skipping round")
        return None


async def _release_lock(token: str) -> None:
    try:
        redis = await get_redis_client()
        await redis.async_connection.eval(RELEASE_LOCK, 1, DISPATCH_LOCK_KEY, token)
    except Exception:
        # 锁有 TTL（略小于 tick），释放失败最多让下一轮晚一个 tick 开始。
        logger.warning("scheduled_task: dispatch lock release failed (will expire by TTL)")


# ── 一轮 ─────────────────────────────────────────────────────────────


async def run_cycle(now: datetime | None = None, *, dispatch=None, notify=None) -> CycleStats:
    """计划轮次：回收过期租约 → 枚举到期 → 逐条判定/写回/派发 → 运行监督。"""
    now = now or _now()
    conf = _conf()
    stats = CycleStats()

    lock_token = await _acquire_lock(conf.dispatch_lock_ttl_seconds)
    if lock_token is None:
        stats.lock_skipped = True
        return stats

    dispatch = dispatch or _dispatch
    notify = notify or _notify

    try:
        stats.reclaimed = await _reclaim_expired(now, notify)

        # 跨租户枚举（beat tick 没有租户 header，见 design §5 坑 10）。
        with bypass_tenant_filter():
            due = await ScheduledTaskDao.alist_due(now)
            running = await ScheduledTaskDao.acount_running(now)
        room = cap_room(running, conf.global_max_running)
        stats.due = len(due)

        for task in due:
            previous_tenant = get_current_tenant_id()
            set_current_tenant_id(task.tenant_id)
            try:
                used = await _process_task(task, now, room, stats, dispatch, notify)
                room -= used
            except Exception:
                # 单条失败不拖垮整轮（下一 tick 会重新判定它）。
                logger.exception("scheduled_task: processing task {} failed", task.id)
                stats.dispatch_errors += 1
            finally:
                set_current_tenant_id(previous_tenant)

        stats.observed = await observe_runs(now, notify=notify)
    finally:
        await _release_lock(lock_token)

    logger.info("schedule.scan.round {}", stats.as_dict())
    return stats


def _next_fire_for(task: ScheduledTask):
    """给 planner 注入的"下一跳"回调；一次性任务不会调用它（keep 语义）。"""

    def _next(anchor: datetime) -> datetime:
        aware = anchor.replace(tzinfo=SERVER_TZ) if anchor.tzinfo is None else anchor
        return next_runs(task.cron_expression, aware, 1)[0].replace(tzinfo=None)

    return _next


def _snapshot(task: ScheduledTask) -> TaskSnapshot:
    return TaskSnapshot(
        id=task.id,
        schedule_type=ScheduleType(task.schedule_type),
        next_run_at=task.next_run_at,
        lease_expires_at=task.lease_expires_at,
        status=TaskStatus(task.status),
        last_status=RunStatus(task.last_status) if task.last_status else None,
        last_planned_at=task.last_planned_at,
        blocked_reason=task.blocked_reason,
        name=task.name,
        created_by=task.created_by,
        run_identity_user_id=task.run_identity_user_id,
        service_account_id=task.service_account_id,
    )


async def _process_task(task, now, room, stats: CycleStats, dispatch, notify) -> int:
    """处理一条到期任务；返回本轮占用的并发名额（0 或 1）。"""
    recheck = await trigger_guard.recheck_task(task)
    decision = classify(
        _snapshot(task),
        now,
        recheck,
        cap_room=room,
        trigger_kind=TriggerKind.SCHEDULED,
        next_fire=_next_fire_for(task),
    )
    stats.reasons.append(f"{task.id}:{decision.variant.value}:{decision.reason.value if decision.reason else ''}")

    if decision.variant is PlanVariant.NORMAL:
        token = generate_uuid()
        lease = now + timedelta(seconds=_conf().run_timeout_seconds)
        claimed = await ScheduledTaskDao.aclaim_lease(
            task.id,
            token=token,
            now=now,
            lease_expires_at=lease,
            next_run_at=decision.next_run_at,
            planned_at=decision.last_planned_at,
            trace_id=token,
        )
        if claimed != 1:
            # 另一个入口（立即运行/事件触发）刚抢走，或槽位已被别的轮次处理。
            logger.info("scheduled_task: task {} lost the claim race; skipping", task.id)
            return 0
        stats.claimed += 1
        try:
            await dispatch(task, trace_id=token)
        except Exception:
            logger.exception("scheduled_task: dispatch failed for task {}", task.id)
            stats.dispatch_errors += 1
            await _finalize(
                task,
                token=token,
                status=RunStatus.FAILED,
                error="dispatch failed",
                now=now,
                notify=notify,
                count_failure=True,
            )
        return 1

    if decision.variant is PlanVariant.SKIP:
        await ScheduledTaskDao.amark_skipped(
            task.id,
            slot=task.next_run_at,
            next_run_at=decision.next_run_at,
            reason=decision.last_error or PlanReason.OVERLAP.value,
            now=now,
        )
        stats.skipped += 1
        return 0

    if decision.variant is PlanVariant.FAIL:
        await _apply_failure(task, decision, now, notify)
        stats.failed += 1
        return 0

    # DEFER（含一次性任务等待上线）：不消费任何计划状态。
    if decision.mark_offline:
        await ScheduledTaskDao.aset_blocked(task.id, BlockedReason.APP_OFFLINE, now=now)
    stats.deferred += 1
    return 0


async def _apply_failure(task, decision, now, notify) -> None:
    """计划轮次的失败记账 + 首败/阈值/删除三类通知（每轮至多一条）。"""
    updated = await ScheduledTaskDao.amark_failed(
        task.id,
        slot=task.next_run_at,
        next_run_at=decision.next_run_at,
        reason=decision.last_error or PlanReason.IDENTITY_UNAVAILABLE.value,
        now=now,
        disable=decision.disable,
    )
    if updated != 1:
        return

    threshold = _conf().consecutive_failure_threshold
    prior = int(task.consecutive_fail_count or 0)
    disabled_at_threshold = False
    if not decision.disable and prior + 1 >= threshold:
        await ScheduledTaskDao.aupdate_fields(task.id, {"status": TaskStatus.DISABLED.value}, tenant_id=task.tenant_id)
        disabled_at_threshold = True

    if decision.disable:
        # AC-31：应用已删除 —— fail-closed 并自动停用，恒通知。
        await notify(
            task,
            ACTION_FAILED,
            "定时任务已自动停用",
            f"任务「{task.name}」的目标应用已被删除，已自动停用。",
        )
    elif prior == 0:
        # AC-54：同一连续失败序列只发首败一条。
        await notify(
            task,
            ACTION_FAILED,
            "定时任务运行失败",
            f"任务「{task.name}」本次运行失败：{decision.last_error or 'unknown'}",
        )
    elif disabled_at_threshold:
        await notify(
            task,
            ACTION_FAILED,
            "定时任务已自动停用",
            f"任务「{task.name}」连续失败 {prior + 1} 次，已自动停用。",
        )


async def _reclaim_expired(now: datetime, notify) -> int:
    """租约回收（AC-15）：判失败、释放互斥，并做与计划轮次同口径的通知决策。"""
    threshold = _conf().consecutive_failure_threshold
    reclaimed = await ScheduledTaskDao.areclaim_expired(now)
    for row in reclaimed:
        if row["fail_count_before"] + 1 >= threshold:
            await ScheduledTaskDao.aupdate_fields(
                row["id"], {"status": TaskStatus.DISABLED.value}, tenant_id=row["tenant_id"]
            )
        if row["fail_count_before"] == 0:
            await notify(
                _LightTask(row),
                ACTION_FAILED,
                "定时任务运行失败",
                f"任务「{row['name']}」运行超时，已判定失败。",
            )
        elif row["fail_count_before"] + 1 == threshold:
            await notify(
                _LightTask(row),
                ACTION_FAILED,
                "定时任务已自动停用",
                f"任务「{row['name']}」连续失败 {threshold} 次，已自动停用。",
            )
    return len(reclaimed)


@dataclass
class _LightTask:
    """回收路径只带必要字段（行已不在会话里），供 `_notify` 统一取用。"""

    _row: dict

    @property
    def id(self):
        return self._row["id"]

    @property
    def tenant_id(self):
        return self._row["tenant_id"]

    @property
    def name(self):
        return self._row["name"]

    @property
    def created_by(self):
        return self._row["created_by"]


# ── 派发 ─────────────────────────────────────────────────────────────


async def _dispatch(task: ScheduledTask, *, trace_id: str) -> None:
    """按应用类型派发一轮（工作流走 RedisCallback + execute_workflow；助手走自有运行器）。"""
    if task.app_type == FlowType.WORKFLOW.value:
        await _dispatch_workflow(task, trace_id)
        return
    from bisheng.worker.scheduled_task.assistant_runner import execute_assistant_run

    execute_assistant_run.apply_async(args=[task.id, trace_id], queue="celery")


async def _dispatch_workflow(task: ScheduledTask, trace_id: str) -> None:
    """F054 序列：预写 data → WAITING → 派到绑定节点（**显式 queue**）。"""
    from bisheng.database.models.flow_version import FlowVersionDao
    from bisheng.worker.workflow.redis_callback import RedisCallback
    from bisheng.worker.workflow.tasks import execute_workflow, workflow_stateful_worker
    from bisheng.workflow.common.workflow import WorkflowStatus

    flow = await FlowDao.aget_flow_by_id(str(task.app_id))
    if flow is None:
        raise RuntimeError(f"flow {task.app_id} disappeared before dispatch")

    callback = RedisCallback(trace_id, str(task.app_id), "", task.run_identity_user_id, tenant_id=task.tenant_id)
    callback.set_workflow_data(copy.deepcopy(flow.data))
    callback.set_workflow_status(WorkflowStatus.WAITING.value)

    # 版本标识：ONLINE 下禁切版本/改数据，故当前版本即本次运行所用版本（AC-52）。
    version = FlowVersionDao.get_version_by_flow(str(task.app_id))
    if version is not None:
        await ScheduledTaskDao.aupdate_fields(
            task.id, {"last_app_version_id": str(version.id)}, tenant_id=task.tenant_id
        )

    worker_node = workflow_stateful_worker.find_task_node_sync(trace_id)
    execute_workflow.apply_async([trace_id, str(task.app_id), "", task.run_identity_user_id], queue=worker_node)


# ── 运行监督（并入 tick 的无状态过，design 决策 6） ────────────────────


async def observe_runs(now: datetime | None = None, *, read_state=None, notify=None) -> int:
    """对持有未过期租约的任务读运行态：终态回写、INPUT 处理、超时回收留给 run_cycle。

    助手运行器自己回写终态（design 决策 5），故本函数只观察**工作流**运行。
    """
    now = now or _now()
    read_state = read_state or _read_workflow_state
    notify = notify or _notify

    with bypass_tenant_filter():
        tasks = await ScheduledTaskDao.alist_with_live_lease(now)

    observed = 0
    for task in tasks:
        if task.app_type != FlowType.WORKFLOW.value or not task.last_trace_id:
            continue
        previous_tenant = get_current_tenant_id()
        set_current_tenant_id(task.tenant_id)
        try:
            state = await read_state(task.last_trace_id)
            if not state:
                continue
            status = state.get("status")
            if status == "SUCCESS":
                await _finalize(
                    task, token=task.run_lease_token, status=RunStatus.SUCCESS, error=None, now=now, notify=notify
                )
            elif status == "FAILED":
                await _finalize(
                    task,
                    token=task.run_lease_token,
                    status=RunStatus.FAILED,
                    error=(state.get("reason") or "run failed")[:480],
                    now=now,
                    notify=notify,
                    count_failure=True,
                )
            elif status == "INPUT":
                await _handle_input(task, notify)
            else:
                continue
            observed += 1
        except Exception:
            logger.exception("scheduled_task: observing task {} failed", task.id)
        finally:
            set_current_tenant_id(previous_tenant)
    return observed


async def _read_workflow_state(trace_id: str) -> dict | None:
    """读运行的 Redis 终态（工作流）。异常按"读不到"处理，下一 tick 再看。"""
    from bisheng.core.cache.redis_manager import get_redis_client_sync

    try:
        return get_redis_client_sync().get(f"workflow:{trace_id}:status")
    except Exception:
        logger.warning("scheduled_task: reading run state failed for trace {}", trace_id)
        return None


async def _handle_input(task: ScheduledTask, notify) -> None:
    """INPUT 暂停态：有预设输入则按 F054 协议自动提交；否则置 needs_input 并通知一次。"""
    from bisheng.worker.workflow.redis_callback import RedisCallback
    from bisheng.worker.workflow.tasks import continue_workflow, workflow_stateful_worker
    from bisheng.workflow.common.workflow import WorkflowStatus

    callback = RedisCallback(
        task.last_trace_id, str(task.app_id), "", task.run_identity_user_id, tenant_id=task.tenant_id
    )
    # 事件列表是 Redis list（`lpop` 消费）：暂停中的运行没有别的消费者，这里读空它
    # 是安全的；`Category == 'input'` 即 INPUT 节点事件（WorkflowEventType.UserInput）。
    input_event = None
    while True:
        response = callback.get_workflow_response()
        if response is None:
            break
        if getattr(response, "category", None) == "input":
            input_event = response
            break
    if input_event is None:
        return

    node_id = input_event.message.get("node_id")
    input_schema = input_event.message.get("input_schema") or {}
    payload, error = _build_preset_payload(input_schema, task.preset_inputs)

    if not task.preset_inputs or error:
        # 没有可用的预设输入：置暂停态（CAS 兼作"已通知"哨兵），由租约到期回收为失败。
        first_time = await ScheduledTaskDao.amark_needs_input(
            task.id,
            token=task.run_lease_token or "",
            error=error or "waiting for human input",
            now=_now(),
        )
        if first_time == 1:
            await notify(
                task,
                ACTION_NEEDS_INPUT,
                "定时任务等待人工输入",
                f"任务「{task.name}」在人工输入节点暂停，未配置可用的预设输入；"
                f"若无人处理，将在运行时长上限后判定失败。",
            )
        return

    # F054 协议：先写 input、再置 INPUT_OVER、再把 continue 派回绑定节点（顺序不可反）。
    callback.set_user_input({node_id: payload})
    callback.set_workflow_status(WorkflowStatus.INPUT_OVER.value)
    worker_node = workflow_stateful_worker.find_task_node_sync(task.last_trace_id)
    continue_workflow.apply_async(
        [task.last_trace_id, str(task.app_id), "", task.run_identity_user_id], queue=worker_node
    )
    logger.info("schedule.run.input_submitted task={} trace={}", task.id, task.last_trace_id)


def _build_preset_payload(input_schema: dict, presets: list | None) -> tuple[dict | None, str]:
    """把任务的预设输入（静态 label/key）映射到本轮运行时 schema 的字段 key。

    文件类输入在一期已被 AC-47 创建期拒绝；此处理论上不会遇到，仍显式报错
    （不静默丢一项，否则运行会"成功"但少填）。
    """
    items = (input_schema or {}).get("value") or []
    by_label: dict[str, dict] = {}
    for item in items:
        label = item.get("value") or item.get("label") or ""
        if label in by_label:
            return None, f"duplicate input label: {label}"
        by_label[label] = item

    values = {}
    for preset in presets or []:
        key = preset.get("key") or preset.get("label")
        if key:
            values[key] = preset.get("value")

    payload: dict = {}
    for label, item in by_label.items():
        if item.get("type") == "file":
            return None, f"file input is not supported on schedules: {label}"
        raw = values.get(label)
        if raw is None and item.get("required"):
            return None, f"missing required preset input: {label}"
        payload[item["key"]] = "" if raw is None else str(raw)
    return payload, ""


# ── 终态回写 ─────────────────────────────────────────────────────────


async def _finalize(
    task,
    *,
    token: str | None,
    status: RunStatus,
    error: str | None,
    now: datetime,
    notify,
    count_failure: bool = False,
) -> None:
    """释放租约 + 写最近结果 + 一次性任务自动停用 + 失败通知 + 运行留痕。"""
    duration_ms = None
    if task.last_run_at:
        duration_ms = max(int((now - task.last_run_at).total_seconds() * 1000), 0)

    threshold = _conf().consecutive_failure_threshold
    prior = int(task.consecutive_fail_count or 0)
    disable = task.schedule_type == ScheduleType.ONCE.value
    disabled_at_threshold = False
    if count_failure and not disable and prior + 1 >= threshold:
        disable = True
        disabled_at_threshold = True

    updated = await ScheduledTaskDao.afinalize(
        task.id,
        token=token,
        status=status,
        error=error,
        now=now,
        duration_ms=duration_ms,
        disable=disable,
        reset_fail_count=(status is RunStatus.SUCCESS),
        increment_fail_count=(status is RunStatus.FAILED),
    )
    if updated != 1:
        # 令牌不匹配：租约已被回收或换了主人（例如超时判定先行），本次回写作废。
        logger.warning("scheduled_task: finalize for task {} did not own the lease", task.id)
        return

    if status is RunStatus.FAILED:
        if prior == 0:
            await notify(
                task,
                ACTION_FAILED,
                "定时任务运行失败",
                f"任务「{task.name}」本次运行失败：{error or 'unknown'}",
            )
        elif disabled_at_threshold:
            await notify(
                task,
                ACTION_FAILED,
                "定时任务已自动停用",
                f"任务「{task.name}」连续失败 {prior + 1} 次，已自动停用。",
            )

    await _audit_run(task, status=status, error=error, now=now)


async def _audit_run(task, *, status: RunStatus, error: str | None, now: datetime) -> None:
    """运行留痕（`schedule.run`）：只落库取证、不进审计页白名单（AC-50）。"""
    try:
        await AuditLogDao.ainsert_v2(
            tenant_id=task.tenant_id,
            operator_id=task.run_identity_user_id,
            operator_tenant_id=task.tenant_id,
            action=ScheduledTaskAuditAction.RUN.value,
            target_type=TARGET_TYPE,
            target_id=str(task.id),
            reason=error,
            metadata={"status": status.value, "trace_id": task.last_trace_id},
            object_name=task.name,
        )
    except Exception:
        # 留痕是 best-effort：写失败不得改变运行结果（与遥测 finally 同口径）。
        logger.exception("scheduled_task: run audit write failed for task {}", task.id)


# ── 通知 ─────────────────────────────────────────────────────────────


async def _notify(task, action_code: str, title: str, body: str) -> None:
    """站内信（best-effort）：收件人 = 创建者；不可用时兜底租户管理员/超管（AC-32）。"""
    try:
        recipients = await _resolve_recipients(task)
        if not recipients:
            logger.error(
                "scheduled_task: no recipient for notification of task {} (creator={})",
                task.id,
                getattr(task, "created_by", None),
            )
            return
        from bisheng.core.database import get_async_db_session
        from bisheng.message.domain.models.inbox_message import (
            MessageStatusEnum,
            MessageTypeEnum,
        )
        from bisheng.message.domain.repositories.implementations.inbox_message_read_repository_impl import (
            InboxMessageReadRepositoryImpl,
        )
        from bisheng.message.domain.repositories.implementations.inbox_message_repository_impl import (
            InboxMessageRepositoryImpl,
        )
        from bisheng.message.domain.services.message_service import MessageService

        async with get_async_db_session() as session:
            service = MessageService(
                message_repository=InboxMessageRepositoryImpl(session),
                message_read_repository=InboxMessageReadRepositoryImpl(session),
            )
            await service.send_message(
                content=[{"type": "text", "title": title, "body": body}],
                sender=0,
                message_type=MessageTypeEnum.NOTIFY,
                receiver=recipients,
                status=MessageStatusEnum.APPROVED,
                action_code=action_code,
            )
    except Exception:
        # 通知是 best-effort：失败不得改变调度的记账结果（权威轨迹在 audit_log）。
        logger.exception("scheduled_task: notification failed for task {}", task.id)


async def _resolve_recipients(task) -> list[int]:
    """创建者优先；其账号不可用时兜底本租户管理员 + 全局超管。"""
    creator = int(getattr(task, "created_by", 0) or 0)
    if creator:
        try:
            from bisheng.user.domain.models.user import UserDao

            user = await UserDao.aget_user(creator)
            if user is not None and int(getattr(user, "delete", 0) or 0) == 0:
                return [creator]
        except Exception:
            logger.warning("scheduled_task: creator lookup failed for user {}", creator)

    fallback: list[int] = []
    try:
        from bisheng.tenant.domain.services.inbox_helper import list_global_super_admin_ids

        fallback.extend(await list_global_super_admin_ids())
    except Exception:
        logger.warning("scheduled_task: could not resolve global super admins")
    return sorted(set(fallback))
