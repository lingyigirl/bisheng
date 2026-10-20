"""F077 T-10：调度编排（run_cycle / observe_runs）。

策略：**真实 DAO + 内存 SQLite**（写原语的分支必须走真 SQL 才算数），
派发 / 运行态读取 / 通知全部注入桩——不碰 broker、不碰真实 Redis。

覆盖 design 决策 2/3/4/6 的编排行为：
- 拿到锁才跑；拿不到就整轮跳过（多 beat 实例的轮次互斥）
- NORMAL 的原子 claim→派发；SKIP/FAIL/DEFER 各自的写回与**不写回**
- 全局并发名额按轮内递减（超限的任务本轮不消费）
- 派发异常 → 立即按失败收敛（不能让任务白占 60 分钟租约）
- 监督过：终态回写释放租约 / 失败计数与首败通知 / INPUT 无预设 → needs_input 哨兵只通知一次
"""

from contextlib import asynccontextmanager
from datetime import datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import StaticPool
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from bisheng.core.database.tenant_filter import register_tenant_filter_events
from bisheng.database.models.flow import FlowType
from bisheng.scheduled_task.domain.models import dao as task_dao
from bisheng.scheduled_task.domain.models.dao import ScheduledTaskDao
from bisheng.scheduled_task.domain.models.scheduled_task import (
    BlockedReason,
    RunStatus,
    ScheduledTask,
    ScheduleType,
    TaskStatus,
)
from bisheng.scheduled_task.domain.services import run_service, trigger_guard
from bisheng.scheduled_task.domain.services.trigger_guard import (
    AppState,
    IdentityState,
    RecheckOutcome,
)

TABLES = [ScheduledTask.__table__]
TENANT_ID = 7
NOW = datetime(2026, 10, 16, 9, 0, 0)


@pytest.fixture()
async def engine():
    eng = create_async_engine("sqlite+aiosqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    async with eng.begin() as conn:
        await conn.run_sync(lambda c: ScheduledTask.metadata.create_all(c, tables=TABLES))
    register_tenant_filter_events()
    yield eng
    await eng.dispose()


@pytest.fixture()
async def patched_db(engine, monkeypatch):
    @asynccontextmanager
    async def _factory():
        async with AsyncSession(bind=engine, expire_on_commit=False) as session:
            yield session

    monkeypatch.setattr(task_dao, "get_async_db_session", _factory)
    yield engine


@pytest.fixture(autouse=True)
async def _tenant_ctx():
    from bisheng.core.context.tenant import set_current_tenant_id

    set_current_tenant_id(TENANT_ID)
    yield
    set_current_tenant_id(None)


@pytest.fixture()
def lock(monkeypatch):
    """默认拿到锁；用例可改写为 None 来验证"整轮跳过"。"""
    state = {"token": "lock-token"}

    async def _acquire(ttl):
        return state["token"]

    async def _release(token):
        return None

    monkeypatch.setattr(run_service, "_acquire_lock", _acquire)
    monkeypatch.setattr(run_service, "_release_lock", _release)
    return state


@pytest.fixture()
def recheck(monkeypatch):
    state = {"outcome": RecheckOutcome(app_state=AppState.OK, identity_state=IdentityState.OK)}

    async def _recheck(task):
        return state["outcome"]

    monkeypatch.setattr(trigger_guard, "recheck_task", _recheck)
    return state


@pytest.fixture(autouse=True)
def captured_audits(monkeypatch):
    """审计写入打桩：单测不得落真实 DB；同时暴露写入内容供断言。"""
    direct: list[dict] = []
    batches: list[list] = []

    async def _insert_v2(cls, **kwargs):
        direct.append(kwargs)

    async def _insert_many(cls, entries):
        batches.append(list(entries))

    monkeypatch.setattr(run_service.AuditLogDao, "ainsert_v2", classmethod(_insert_v2))
    monkeypatch.setattr(run_service.AuditLogDao, "ainsert_audit_logs", classmethod(_insert_many))
    return {"direct": direct, "batches": batches}


async def _task(**overrides) -> ScheduledTask:
    base = {
        "name": "nightly",
        "app_id": "1001",
        "app_type": FlowType.WORKFLOW.value,
        "service_account_id": 5,
        "run_identity_user_id": 501,
        "created_by": 42,
        "next_run_at": NOW - timedelta(minutes=1),
        "cron_expression": "0 9 * * *",
    }
    base.update(overrides)
    return await ScheduledTaskDao.acreate(ScheduledTask(**base))


async def _read(engine, task_id: int) -> ScheduledTask:
    async with AsyncSession(bind=engine, expire_on_commit=False) as session:
        return (await session.execute(select(ScheduledTask).where(ScheduledTask.id == task_id))).scalars().one()


# ── 纯函数：预设输入映射 ─────────────────────────────────────────────


def test_build_preset_payload_maps_labels_to_runtime_keys():
    schema = {
        "tab": "form_input",
        "value": [
            {"key": "rt_1", "value": "客户名称", "type": "input", "required": True},
            {"key": "rt_2", "value": "备注", "type": "input"},
        ],
    }
    payload, err = run_service._build_preset_payload(
        schema, [{"key": "客户名称", "value": "甲公司"}, {"key": "备注", "value": "ok"}]
    )
    assert err == "" and payload == {"rt_1": "甲公司", "rt_2": "ok"}


def test_build_preset_payload_rejects_missing_required_and_file_inputs():
    schema = {"value": [{"key": "rt_1", "value": "客户名称", "type": "input", "required": True}]}
    payload, err = run_service._build_preset_payload(schema, [])
    assert payload is None and "missing required" in err

    schema_file = {"value": [{"key": "rt_f", "value": "附件", "type": "file"}]}
    payload, err = run_service._build_preset_payload(schema_file, [{"key": "附件", "value": "x"}])
    assert payload is None and "file input" in err


# ── run_cycle ───────────────────────────────────────────────────────


async def test_normal_claims_and_dispatches(patched_db, lock, recheck):
    task = await _task()
    dispatched = []

    async def _dispatch(t, *, trace_id):
        dispatched.append((t.id, trace_id))

    stats = await run_service.run_cycle(NOW, dispatch=_dispatch, notify=_noop_notify)

    assert stats.claimed == 1 and stats.due == 1
    assert dispatched and dispatched[0][0] == task.id
    row = await _read(patched_db, task.id)
    assert row.run_lease_token == dispatched[0][1]
    assert row.next_run_at.replace(tzinfo=None) == datetime(2026, 10, 17, 9, 0), "推进到下一个计划点"
    # AC-52：claim 即本次运行开始时间；此前该列从未被写入。
    assert row.last_run_at == NOW
    assert row.run_started_at == NOW


async def test_offline_periodic_task_is_skipped_without_failure(patched_db, lock, recheck):
    task = await _task()
    recheck["outcome"] = RecheckOutcome(app_state=AppState.OFFLINE, identity_state=IdentityState.OK)
    stats = await run_service.run_cycle(NOW, dispatch=_never, notify=_noop_notify)

    assert stats.skipped == 1, "应用下线在计划轮次记「已跳过」，不是失败（AC-16）"
    row = await _read(patched_db, task.id)
    assert row.last_status == RunStatus.SKIPPED.value
    assert row.consecutive_fail_count == 0
    assert row.run_lease_token is None


async def test_identity_failure_counts_and_notifies_first_failure(patched_db, lock, recheck):
    task = await _task()
    recheck["outcome"] = RecheckOutcome(app_state=AppState.OK, identity_state=IdentityState.NO_USE_APP)
    notified = []

    async def _notify(t, action, title, body):
        notified.append((action, title))

    stats = await run_service.run_cycle(NOW, dispatch=_never, notify=_notify)

    assert stats.failed == 1
    row = await _read(patched_db, task.id)
    assert row.last_status == RunStatus.FAILED.value
    assert row.consecutive_fail_count == 1
    assert "no_use_app" in (row.last_error or "")
    assert notified == [(run_service.ACTION_FAILED, "定时任务运行失败")], "首败恰好一条"


async def test_once_task_offline_is_deferred_without_consuming(patched_db, lock, recheck):
    task = await _task(schedule_type=ScheduleType.ONCE.value, run_at=NOW - timedelta(minutes=1))
    recheck["outcome"] = RecheckOutcome(app_state=AppState.OFFLINE, identity_state=IdentityState.OK)
    stats = await run_service.run_cycle(NOW, dispatch=_never, notify=_noop_notify)

    assert stats.deferred == 1
    row = await _read(patched_db, task.id)
    assert row.next_run_at.replace(tzinfo=None) == NOW - timedelta(minutes=1), "等待上线不消耗唯一机会"
    assert row.blocked_reason == BlockedReason.APP_OFFLINE.value
    assert row.last_status is None, "不污染「上次结果」列"


async def test_global_cap_defers_everything(patched_db, lock, recheck, monkeypatch):
    await _task()
    await _task(name="second")

    async def _full(now):
        return 10  # 已到 global_max_running 默认值

    monkeypatch.setattr(ScheduledTaskDao, "acount_running", _full)
    dispatch_calls = []

    async def _dispatch(t, *, trace_id):
        dispatch_calls.append(t.id)

    stats = await run_service.run_cycle(NOW, dispatch=_dispatch, notify=_noop_notify)
    assert stats.deferred == 2 and not dispatch_calls, "名额不足时本轮不消费、不派发（AC-12）"


async def test_lock_not_acquired_skips_whole_round(patched_db, lock, recheck):
    await _task()
    lock["token"] = None
    stats = await run_service.run_cycle(NOW, dispatch=_never, notify=_noop_notify)
    assert stats.lock_skipped is True and stats.due == 0


async def test_dispatch_failure_is_finalized_as_failed(patched_db, lock, recheck, captured_audits):
    task = await _task()
    seen = {}

    async def _boom(t, *, trace_id):
        seen["trace_id"] = trace_id
        raise RuntimeError("broker down")

    notified = []

    async def _notify(t, action, title, body):
        notified.append(title)

    stats = await run_service.run_cycle(NOW, dispatch=_boom, notify=_notify)
    assert stats.dispatch_errors == 1
    row = await _read(patched_db, task.id)
    assert row.last_status == RunStatus.FAILED.value
    assert row.run_lease_token is None, "派发失败立即释放租约，不让任务白占 60 分钟"
    assert row.consecutive_fail_count == 1
    assert notified, "首败通知"
    # 派发失败的 schedule.run 必须用本次 claim 的 trace，而不是上一轮的内存值。
    assert captured_audits["direct"], "派发失败也要写运行留痕"
    assert captured_audits["direct"][-1]["metadata"]["trace_id"] == seen["trace_id"]
    assert row.last_duration_ms is not None, "AC-52：派发失败也有耗时（0ms 起）"


async def test_dispatch_workflow_persists_version_identifier(patched_db, monkeypatch):
    """AC-52：工作流派发时把当前 FlowVersion.id 落到 last_app_version_id。"""
    from types import SimpleNamespace

    import bisheng.database.models.flow_version as flow_version_module
    import bisheng.worker.workflow.redis_callback as redis_callback_module
    import bisheng.worker.workflow.tasks as workflow_tasks_module

    task = await _task()
    captured: dict = {}

    class _FakeFlow:
        data = {"nodes": [], "edges": []}

    class _FakeCallback:
        def __init__(self, *args, **kwargs):
            pass

        def set_workflow_data(self, data):
            captured["data"] = data

        def set_workflow_status(self, status):
            captured["status"] = status

    class _FakeWorker:
        @staticmethod
        def find_task_node_sync(trace_id):
            return "workflow_celery"

    class _FakeExecute:
        @staticmethod
        def apply_async(args, queue=None):
            captured["dispatch"] = (args, queue)

    async def _get_flow(flow_id):
        return _FakeFlow()

    async def _update(task_id, fields, *, tenant_id):
        captured["fields"] = fields
        return 1

    monkeypatch.setattr(run_service.FlowDao, "aget_flow_by_id", staticmethod(_get_flow))
    monkeypatch.setattr(
        flow_version_module.FlowVersionDao,
        "get_version_by_flow",
        staticmethod(lambda flow_id: SimpleNamespace(id=123)),
    )
    monkeypatch.setattr(redis_callback_module, "RedisCallback", _FakeCallback)
    monkeypatch.setattr(workflow_tasks_module, "workflow_stateful_worker", _FakeWorker())
    monkeypatch.setattr(workflow_tasks_module, "execute_workflow", _FakeExecute())
    monkeypatch.setattr(run_service.ScheduledTaskDao, "aupdate_fields", _update)

    await run_service._dispatch_workflow(task, "trace-version")

    assert captured["fields"] == {"last_app_version_id": "123"}
    assert captured["dispatch"] == (
        ["trace-version", str(task.app_id), "", task.run_identity_user_id],
        "workflow_celery",
    )


async def test_plan_failure_writes_system_audit(patched_db, lock, recheck, captured_audits):
    task = await _task()
    recheck["outcome"] = RecheckOutcome(app_state=AppState.OK, identity_state=IdentityState.NO_USE_APP)

    await run_service.run_cycle(NOW, dispatch=_never, notify=_noop_notify)

    row = await _read(patched_db, task.id)
    assert row.last_status == RunStatus.FAILED.value
    assert len(captured_audits["batches"]) == 1
    entry = captured_audits["batches"][0][0]
    assert entry.operator_id == 0, "系统自动处置以系统身份留痕（AC-50）"
    assert entry.operator_tenant_id == run_service.DEFAULT_TENANT_ID
    assert entry.action == "schedule.run"
    assert entry.audit_metadata["status"] == RunStatus.FAILED.value
    assert entry.audit_metadata["disposition"] == "plan_failure"
    assert entry.audit_metadata["auto_disabled"] is False
    assert entry.audit_metadata["slot"] is not None


async def test_threshold_auto_disable_is_system_audited(patched_db, lock, recheck, captured_audits):
    task = await _task(consecutive_fail_count=4)
    recheck["outcome"] = RecheckOutcome(app_state=AppState.OK, identity_state=IdentityState.NO_USE_APP)

    await run_service.run_cycle(NOW, dispatch=_never, notify=_noop_notify)

    row = await _read(patched_db, task.id)
    assert row.status == TaskStatus.DISABLED.value
    entry = captured_audits["batches"][0][0]
    assert entry.operator_id == 0
    assert entry.audit_metadata["auto_disabled"] is True
    assert entry.audit_metadata["fail_count"] == 5


async def test_skip_does_not_write_run_audit(patched_db, lock, recheck, captured_audits):
    await _task()
    recheck["outcome"] = RecheckOutcome(app_state=AppState.OFFLINE, identity_state=IdentityState.OK)

    await run_service.run_cycle(NOW, dispatch=_never, notify=_noop_notify)

    assert captured_audits["batches"] == [] and captured_audits["direct"] == [], "跳过不是一次运行"


async def test_reclaim_expired_writes_duration_and_system_audit(patched_db, lock, captured_audits):
    task = await _task()
    started = NOW - timedelta(seconds=30)
    await ScheduledTaskDao.aclaim_lease(
        task.id,
        token="tok-reclaim",
        now=started,
        lease_expires_at=NOW - timedelta(seconds=1),
        next_run_at=NOW + timedelta(days=1),
        planned_at=NOW - timedelta(minutes=1),
        trace_id="trace-reclaim",
    )

    await run_service.run_cycle(NOW, dispatch=_never, notify=_noop_notify)

    row = await _read(patched_db, task.id)
    assert row.last_status == RunStatus.FAILED.value
    assert row.last_run_at == started
    assert row.last_duration_ms == 30000
    entry = captured_audits["batches"][0][0]
    assert entry.operator_id == 0
    assert entry.audit_metadata["disposition"] == "lease_timeout"
    assert entry.audit_metadata["duration_ms"] == 30000
    assert entry.audit_metadata["trace_id"] == "trace-reclaim"


async def test_reclaim_expired_runs_without_tenant_context(patched_db, lock, captured_audits, monkeypatch):
    """多租户 beat 没有租户上下文：回收必须先 bypass，否则整轮 NoTenantContextError。"""
    from bisheng.common.services.config_service import settings
    from bisheng.core.config.multi_tenant import MultiTenantConf
    from bisheng.core.context.tenant import set_current_tenant_id

    monkeypatch.setattr(settings, "multi_tenant", MultiTenantConf(enabled=True))
    task = await _task()
    await ScheduledTaskDao.aclaim_lease(
        task.id,
        token="tok-mt",
        now=NOW - timedelta(seconds=10),
        lease_expires_at=NOW - timedelta(seconds=1),
        next_run_at=NOW + timedelta(days=1),
        planned_at=NOW - timedelta(minutes=1),
        trace_id="trace-mt",
    )
    set_current_tenant_id(None)
    try:
        stats = await run_service.run_cycle(NOW, dispatch=_never, notify=_noop_notify)
    finally:
        set_current_tenant_id(TENANT_ID)

    assert stats.reclaimed == 1
    row = await _read(patched_db, task.id)
    assert row.last_status == RunStatus.FAILED.value
    assert captured_audits["batches"], "回收审计即使无租户上下文也要落库"


async def test_run_now_dispatch_failure_releases_lease_without_failure_count(patched_db, recheck, captured_audits):
    task = await _task()

    async def _boom(t, *, trace_id):
        raise RuntimeError("broker down")

    with pytest.raises(RuntimeError):
        await run_service.run_now(task.id, dispatch=_boom, notify=_noop_notify)

    row = await _read(patched_db, task.id)
    assert row.run_lease_token is None
    assert row.last_status == RunStatus.FAILED.value
    assert row.consecutive_fail_count == 0, "AC-17：人工试跑失败不计连续失败"
    assert row.last_duration_ms is not None
    assert captured_audits["direct"], "人工派发失败也要留痕"


async def test_run_now_dispatch_failure_keeps_once_task_enabled(patched_db, recheck, captured_audits):
    task = await _task(
        schedule_type=ScheduleType.ONCE.value,
        run_at=NOW + timedelta(days=1),
        next_run_at=NOW + timedelta(days=1),
    )

    async def _boom(t, *, trace_id):
        raise RuntimeError("broker down")

    with pytest.raises(RuntimeError):
        await run_service.run_now(task.id, dispatch=_boom, notify=_noop_notify)

    row = await _read(patched_db, task.id)
    assert row.status == TaskStatus.ENABLED.value, "人工试跑失败不消耗一次性任务的唯一机会"


# ── observe_runs ────────────────────────────────────────────────────


async def _claimed_task(engine) -> ScheduledTask:
    task = await _task()
    await ScheduledTaskDao.aclaim_lease(
        task.id,
        token="tok",
        now=NOW,
        lease_expires_at=NOW + timedelta(hours=1),
        next_run_at=NOW + timedelta(days=1),
        planned_at=NOW - timedelta(minutes=1),
        trace_id="trace-1",
    )
    return task


async def test_observe_success_finalizes_and_clears_lease(patched_db, lock):
    task = await _claimed_task(patched_db)

    async def _read_state(trace_id):
        return {"status": "SUCCESS", "reason": None}

    observed = await run_service.observe_runs(NOW, read_state=_read_state, notify=_noop_notify)
    assert observed == 1
    row = await _read(patched_db, task.id)
    assert row.last_status == RunStatus.SUCCESS.value
    assert row.run_lease_token is None and row.lease_expires_at is None
    assert row.consecutive_fail_count == 0


async def test_observe_failure_increments_count_and_notifies(patched_db, lock):
    task = await _claimed_task(patched_db)
    notified = []

    async def _read_state(trace_id):
        return {"status": "FAILED", "reason": "llm timeout"}

    async def _notify(t, action, title, body):
        notified.append(title)

    await run_service.observe_runs(NOW, read_state=_read_state, notify=_notify)
    row = await _read(patched_db, task.id)
    assert row.last_status == RunStatus.FAILED.value
    assert row.consecutive_fail_count == 1
    assert notified == ["定时任务运行失败"]


async def test_observe_input_without_presets_sets_needs_input_once(patched_db, lock, monkeypatch):
    task = await _claimed_task(patched_db)
    notified = []

    async def _read_state(trace_id):
        return {"status": "INPUT"}

    # 无预设输入时 `_handle_input` 在拿到 input 事件后才会 CAS；这里把事件构造
    # 收敛到一个可注入的薄函数（协议本身的 F054 顺序由 e2e 覆盖）。
    async def _fake_handle_input(t, notify):
        first = await ScheduledTaskDao.amark_needs_input(
            t.id, token=t.run_lease_token or "", error="waiting for human input", now=NOW
        )
        if first == 1:
            await notify(t, run_service.ACTION_NEEDS_INPUT, "定时任务等待人工输入", "…")

    monkeypatch.setattr(run_service, "_handle_input", _fake_handle_input)

    async def _notify(t, action, title, body):
        notified.append(action)

    await run_service.observe_runs(NOW, read_state=_read_state, notify=_notify)
    row = await _read(patched_db, task.id)
    assert row.last_status == RunStatus.NEEDS_INPUT.value, "暂停态（不是终态）"
    assert notified == [run_service.ACTION_NEEDS_INPUT]

    # 第二次观察：哨兵已置位 → 不再通知
    await run_service.observe_runs(NOW, read_state=_read_state, notify=_notify)
    assert notified == [run_service.ACTION_NEEDS_INPUT], "同一待输入序列只通知一次"


# ── 桩 ──────────────────────────────────────────────────────────────


async def _never(task, *, trace_id):
    raise AssertionError("dispatch should not be called")


async def _noop_notify(task, action_code, title, body):
    return None
