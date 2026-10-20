"""F077 T-03：``scheduled_task`` 表的租户回填与原子写原语。

覆盖：
- spec AC-53（任务按租户隔离；``tenant_id`` 由 ``before_flush`` 自动回填——
  模型必须 ``default=None``，见 ``test/tenant/test_tenant_id_default_guard.py``）
- spec AC-11/AC-14/AC-15/AC-16/AC-46 的**状态原语**：claim 的 CAS、槽位校验、
  失败计数、阻塞标记的幂等、租约回收、needs_input 哨兵
- design §4.2 的字段约定与 §5 坑 17（**禁 RETURNING**，一律 ``rowcount``）

与 ``test/open_api/test_service_account_service.py`` 同款做法：函数级内存
SQLite 引擎 + ``monkeypatch`` 掉 ``get_async_db_session``。
"""

from contextlib import asynccontextmanager
from datetime import datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import StaticPool
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from bisheng.core.database.tenant_filter import register_tenant_filter_events
from bisheng.scheduled_task.domain.models import dao as task_dao
from bisheng.scheduled_task.domain.models.dao import ScheduledTaskDao
from bisheng.scheduled_task.domain.models.scheduled_task import (
    BlockedReason,
    RunStatus,
    ScheduledTask,
    ScheduleType,
    TaskStatus,
)

TABLES = [ScheduledTask.__table__]
TENANT_ID = 7
NOW = datetime(2026, 10, 16, 9, 0, 0)


@pytest.fixture()
async def sched_engine():
    engine = create_async_engine(
        "sqlite+aiosqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(lambda sync_conn: ScheduledTask.metadata.create_all(sync_conn, tables=TABLES))
    register_tenant_filter_events()  # 幂等；让 before_flush 的租户回填生效
    yield engine
    await engine.dispose()


@pytest.fixture()
async def patched_db(sched_engine, monkeypatch):
    @asynccontextmanager
    async def _factory():
        async with AsyncSession(bind=sched_engine, expire_on_commit=False) as session:
            yield session

    monkeypatch.setattr(task_dao, "get_async_db_session", _factory)
    yield sched_engine


@pytest.fixture(autouse=True)
async def _tenant_ctx():
    """租户上下文（本文件的用例都是 async，不能用 sync 版 ``tenant_context``
    fixture——它的 token.reset 发生在另一个 Context，teardown 会报
    ``Token was created in a different Context``）。

    置位与复位都在同一个 async 任务内完成；复位直接写 None（不复用 token）。
    """
    from bisheng.core.context.tenant import set_current_tenant_id

    set_current_tenant_id(TENANT_ID)
    yield TENANT_ID
    set_current_tenant_id(None)


def _task(**overrides) -> ScheduledTask:
    base = {
        "name": "nightly-report",
        "app_id": "1001",
        "app_type": 10,  # FlowType.WORKFLOW
        "service_account_id": 5,
        "run_identity_user_id": 501,
        "created_by": 42,
        "next_run_at": NOW - timedelta(minutes=1),
    }
    base.update(overrides)
    return ScheduledTask(**base)


async def _read(sched_engine, task_id: int) -> ScheduledTask:
    async with AsyncSession(bind=sched_engine, expire_on_commit=False) as session:
        return (await session.execute(select(ScheduledTask).where(ScheduledTask.id == task_id))).scalars().one()


async def test_tenant_id_autofill_and_defaults(patched_db):
    """AC-53：未显式给 tenant_id 的行落库时由当前租户上下文回填；默认值符合 §4.2。"""
    task = await ScheduledTaskDao.acreate(_task())

    assert task.tenant_id == TENANT_ID
    assert task.status == TaskStatus.ENABLED.value
    assert task.schedule_type == ScheduleType.CRON.value
    assert task.consecutive_fail_count == 0
    assert task.run_lease_token is None and task.last_status is None


async def test_claim_lease_cas_and_expired_lease(patched_db):
    """AC-14/AC-15：claim 是单条 CAS——抢到一次；持租约期间抢不到；租约过期视同空闲。"""
    task = await ScheduledTaskDao.acreate(_task())
    lease = NOW + timedelta(hours=1)
    next_at = NOW + timedelta(days=1)

    first = await ScheduledTaskDao.aclaim_lease(
        task.id,
        token="tok-a",
        now=NOW,
        lease_expires_at=lease,
        next_run_at=next_at,
        planned_at=NOW - timedelta(minutes=1),
        trace_id="trace-1",
    )
    assert first == 1
    row = await _read(patched_db, task.id)
    assert row.run_lease_token == "tok-a"
    assert row.next_run_at.replace(tzinfo=None) == next_at
    assert row.last_trace_id == "trace-1"

    second = await ScheduledTaskDao.aclaim_lease(
        task.id,
        token="tok-b",
        now=NOW,
        lease_expires_at=lease,
        next_run_at=next_at,
        planned_at=NOW,
        trace_id="trace-2",
    )
    assert second == 0, "持租约期间必须抢不到（单任务互斥）"

    # 租约过期 + 新槽位到期 → 视同空闲（回收 UPDATE 没跑也不造成永久互斥）
    row.lease_expires_at = NOW - timedelta(minutes=1)
    row.next_run_at = NOW - timedelta(minutes=1)
    async with AsyncSession(bind=patched_db) as session:
        await session.merge(row)
        await session.commit()
    third = await ScheduledTaskDao.aclaim_lease(
        task.id,
        token="tok-c",
        now=NOW,
        lease_expires_at=lease,
        next_run_at=next_at,
        planned_at=NOW - timedelta(minutes=1),
        trace_id="trace-3",
    )
    assert third == 1


async def test_claim_keep_next_run_at_for_once_and_run_now(patched_db):
    """AC-08/AC-17：一次性任务与立即运行不推进 next_run_at、不写 last_planned_at。"""
    task = await ScheduledTaskDao.acreate(
        _task(schedule_type=ScheduleType.ONCE.value, run_at=NOW - timedelta(minutes=1))
    )
    assert (
        await ScheduledTaskDao.aclaim_lease(
            task.id,
            token="tok",
            now=NOW,
            lease_expires_at=NOW + timedelta(hours=1),
            next_run_at=None,
            planned_at=NOW,
            trace_id="t",
            keep_next_run_at=True,
        )
        == 1
    )
    row = await _read(patched_db, task.id)
    assert row.next_run_at.replace(tzinfo=None) == NOW - timedelta(minutes=1)
    assert row.last_planned_at is None


async def test_mark_skipped_slot_guard(patched_db):
    """AC-11：跳过的写入带槽位校验——槽位不符（已被别的轮次处理）时不得改写。"""
    slot = NOW - timedelta(minutes=1)
    task = await ScheduledTaskDao.acreate(_task(next_run_at=slot))
    next_at = NOW + timedelta(minutes=1)

    stale = await ScheduledTaskDao.amark_skipped(
        task.id, slot=NOW - timedelta(minutes=5), next_run_at=next_at, reason="overlap", now=NOW
    )
    assert stale == 0

    ok = await ScheduledTaskDao.amark_skipped(task.id, slot=slot, next_run_at=next_at, reason="overlap", now=NOW)
    assert ok == 1
    row = await _read(patched_db, task.id)
    assert row.last_status == RunStatus.SKIPPED.value
    assert row.last_error == "overlap"
    assert row.next_run_at.replace(tzinfo=None) == next_at
    assert row.consecutive_fail_count == 0, "跳过不计失败"
    assert row.run_lease_token is None, "跳过不占租约"


async def test_mark_failed_counts_and_disable(patched_db):
    """AC-22/AC-31：身份不可用等按失败记账并推进计数；应用已删除 → 一并自动停用。"""
    slot = NOW - timedelta(minutes=1)
    task = await ScheduledTaskDao.acreate(_task(next_run_at=slot))

    assert (
        await ScheduledTaskDao.amark_failed(task.id, slot=slot, next_run_at=NOW, reason="identity unavailable", now=NOW)
        == 1
    )
    row = await _read(patched_db, task.id)
    assert row.last_status == RunStatus.FAILED.value
    assert row.consecutive_fail_count == 1
    assert row.status == TaskStatus.ENABLED.value

    row.next_run_at = slot
    async with AsyncSession(bind=patched_db) as session:
        await session.merge(row)
        await session.commit()
    assert (
        await ScheduledTaskDao.amark_failed(
            task.id, slot=slot, next_run_at=None, reason="app deleted", now=NOW, disable=True
        )
        == 1
    )
    row = await _read(patched_db, task.id)
    assert row.status == TaskStatus.DISABLED.value
    assert row.consecutive_fail_count == 2


async def test_set_blocked_is_idempotent(patched_db):
    """AC-16：一次性任务等待上线用独立字段表达，且重复置位只算一次。"""
    task = await ScheduledTaskDao.acreate(_task(schedule_type=ScheduleType.ONCE.value))

    assert await ScheduledTaskDao.aset_blocked(task.id, BlockedReason.APP_OFFLINE, now=NOW) == 1
    assert await ScheduledTaskDao.aset_blocked(task.id, BlockedReason.APP_OFFLINE, now=NOW) == 0
    row = await _read(patched_db, task.id)
    assert row.blocked_reason == BlockedReason.APP_OFFLINE.value
    assert row.last_status is None, "等待上线不污染「上次结果」列（AC-58）"
    assert row.next_run_at is not None, "等待期间不消耗唯一机会"


async def test_finalize_requires_own_token(patched_db):
    """AC-14：终态回写以本人租约令牌为条件，防止误释放他人租约。"""
    task = await ScheduledTaskDao.acreate(_task())
    await ScheduledTaskDao.aclaim_lease(
        task.id,
        token="own",
        now=NOW,
        lease_expires_at=NOW + timedelta(hours=1),
        next_run_at=NOW + timedelta(days=1),
        planned_at=NOW,
        trace_id="trace-own",
    )

    assert await ScheduledTaskDao.afinalize(task.id, token="other", status=RunStatus.SUCCESS, error=None, now=NOW) == 0
    assert (
        await ScheduledTaskDao.afinalize(
            task.id, token="own", status=RunStatus.SUCCESS, error=None, now=NOW, duration_ms=1234, reset_fail_count=True
        )
        == 1
    )
    row = await _read(patched_db, task.id)
    assert row.last_status == RunStatus.SUCCESS.value
    assert row.last_duration_ms == 1234
    assert row.run_lease_token is None and row.lease_expires_at is None
    assert row.last_finish_at is not None


async def test_mark_needs_input_is_a_onetime_sentinel(patched_db):
    """AC-46：needs_input 兼作「已通知」哨兵——第二次不再返回 1（不重复发信）。"""
    task = await ScheduledTaskDao.acreate(_task())
    await ScheduledTaskDao.aclaim_lease(
        task.id,
        token="tok",
        now=NOW,
        lease_expires_at=NOW + timedelta(hours=1),
        next_run_at=NOW + timedelta(days=1),
        planned_at=NOW,
        trace_id="t",
    )

    assert await ScheduledTaskDao.amark_needs_input(task.id, token="tok", error="waiting input", now=NOW) == 1
    assert await ScheduledTaskDao.amark_needs_input(task.id, token="tok", error="waiting input", now=NOW) == 0


async def test_reclaim_expired_lease_and_running_count(patched_db):
    """AC-15：租约过期由下一个 tick 判失败并释放互斥；过期租约不计入全局并发。"""
    task = await ScheduledTaskDao.acreate(_task(next_run_at=NOW - timedelta(hours=3)))
    assert (
        await ScheduledTaskDao.aclaim_lease(
            task.id,
            token="tok",
            now=NOW - timedelta(hours=2),
            lease_expires_at=NOW - timedelta(minutes=1),  # 已过期
            next_run_at=NOW + timedelta(days=1),
            planned_at=NOW - timedelta(hours=3),
            trace_id="t",
        )
        == 1
    )

    assert await ScheduledTaskDao.acount_running(NOW) == 0, "过期租约不计入并发闸（计数自愈）"
    reclaimed = await ScheduledTaskDao.areclaim_expired(NOW)
    assert [r["id"] for r in reclaimed] == [task.id]

    row = await _read(patched_db, task.id)
    assert row.last_status == RunStatus.FAILED.value
    assert row.run_lease_token is None and row.lease_expires_at is None
    assert row.consecutive_fail_count == 1
    assert "lease expired" in (row.last_error or "")


async def test_count_running_counts_live_leases(patched_db):
    """AC-12：只有未过期租约计入全局并发。"""
    for idx in range(2):
        task = await ScheduledTaskDao.acreate(_task(name=f"t-{idx}"))
        await ScheduledTaskDao.aclaim_lease(
            task.id,
            token=f"tok-{idx}",
            now=NOW,
            lease_expires_at=NOW + timedelta(hours=1),
            next_run_at=NOW + timedelta(days=1),
            planned_at=NOW,
            trace_id=f"trace-{idx}",
        )
    assert await ScheduledTaskDao.acount_running(NOW) == 2
    assert await ScheduledTaskDao.acount_enabled_by_app("1001", 10) == 2


async def test_claim_without_due_for_sync_entries(patched_db):
    """回归（W2 复核 finding-7）：立即运行/事件触发在任务**未到期**时也要能占租约。

    计划轮次必须按到期才可抢（require_due=True，默认）；同步入口用
    require_due=False——否则用户点"立即运行"时（任务通常还没到点）rowcount=0，
    会被调用方误判成"互斥中"而错回 11909。
    """
    task = await ScheduledTaskDao.acreate(_task(next_run_at=NOW + timedelta(days=1)))

    # 计划轮次的 claim 抢不到（未到期）
    assert (
        await ScheduledTaskDao.aclaim_lease(
            task.id,
            token="sched",
            now=NOW,
            lease_expires_at=NOW + timedelta(hours=1),
            next_run_at=NOW + timedelta(days=1),
            planned_at=NOW,
            trace_id="t1",
        )
        == 0
    )
    # 同步入口可以（keep 语义：不推进计划）
    assert (
        await ScheduledTaskDao.aclaim_lease(
            task.id,
            token="manual",
            now=NOW,
            lease_expires_at=NOW + timedelta(hours=1),
            next_run_at=None,
            planned_at=None,
            trace_id="t2",
            keep_next_run_at=True,
            require_due=False,
        )
        == 1
    )
    row = await _read(patched_db, task.id)
    assert row.run_lease_token == "manual"
    assert row.next_run_at.replace(tzinfo=None) == NOW + timedelta(days=1), "立即运行不改变既定计划"


def test_count_enabled_by_app_sync_uses_tenant_scope():
    """同步计数（供删除拦截用）：只数启用中的任务，且受租户过滤约束。"""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session
    from sqlalchemy.pool import StaticPool

    from bisheng.core.context.tenant import set_current_tenant_id
    from bisheng.core.database import tenant_filter

    # 同步 sqlite 引擎 + 内存库（与异步测试同表定义）
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    ScheduledTask.metadata.create_all(engine, tables=TABLES)

    from contextlib import contextmanager

    @contextmanager
    def _session():
        with Session(bind=engine) as session:
            yield session

    original = tenant_filter.__dict__.get("get_sync_db_session")
    set_current_tenant_id(TENANT_ID)
    try:
        import bisheng.core.database as core_db

        core_db.get_sync_db_session = _session  # type: ignore[assignment]
        with Session(bind=engine) as session:
            session.add_all(
                [
                    ScheduledTask(
                        name="a", app_id="1001", app_type=10, service_account_id=5,
                        run_identity_user_id=501, created_by=42, tenant_id=TENANT_ID,
                    ),
                    ScheduledTask(
                        name="b", app_id="1001", app_type=10, service_account_id=5,
                        run_identity_user_id=501, created_by=42, tenant_id=TENANT_ID,
                        status=TaskStatus.DISABLED.value,
                    ),
                ]
            )
            session.commit()

        assert ScheduledTaskDao.count_enabled_by_app_sync("1001", 10) == 1
        assert ScheduledTaskDao.count_enabled_by_app_sync("9999", 10) == 0
    finally:
        if original is not None:
            core_db.get_sync_db_session = original  # type: ignore[assignment]
        set_current_tenant_id(None)
        engine.dispose()
