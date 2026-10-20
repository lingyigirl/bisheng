"""F077 T-15：管理面服务（创建/编辑/启停/删除/立即运行/列表/预览/可用身份）。

策略：真实 DAO + 内存 SQLite；权限、绑定校验、知识校验、审计全部打桩——它们各自
已有独立测试（trigger_guard / knowledge_guard / audit lockstep）。这样本文件锁住的
是**编排与写库**：校验顺序、写入哪些列、何时重算 next_run_at、权限缺省拒绝。
"""

from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import StaticPool
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from bisheng.common.errcode.http_error import NotFoundError, UnAuthorizedError
from bisheng.common.errcode.scheduled_task import (
    ScheduledTaskConfigInvalidError,
    ScheduledTaskCronInvalidError,
    ScheduledTaskIdentityUnavailableError,
    ScheduledTaskLimitExceededError,
)
from bisheng.core.database.tenant_filter import register_tenant_filter_events
from bisheng.database.models.flow import FlowType
from bisheng.scheduled_task.domain.models import dao as task_dao
from bisheng.scheduled_task.domain.models.dao import ScheduledTaskDao
from bisheng.scheduled_task.domain.models.scheduled_task import ScheduledTask, TaskStatus
from bisheng.scheduled_task.domain.schemas.task import (
    PresetInputItem,
    ScheduledTaskCreateReq,
    ScheduledTaskUpdateReq,
)
from bisheng.scheduled_task.domain.services import app_shape_guard, knowledge_guard, task_service, trigger_guard
from bisheng.scheduled_task.domain.services.trigger_guard import IdentityBinding, IdentityState

TABLES = [ScheduledTask.__table__]
TENANT_ID = 7
NOW = datetime(2026, 10, 16, 9, 0, 0)
TZ = ZoneInfo("Asia/Shanghai")


class _User:
    def __init__(self, *, is_global_super: bool = False, tenant_admin: bool = False, user_id: int = 7):
        self.user_id = user_id
        self.tenant_id = TENANT_ID
        self.is_global_super = is_global_super
        self._tenant_admin = tenant_admin

    async def has_tenant_admin(self, tenant_id: int) -> bool:
        return self._tenant_admin


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


@pytest.fixture(autouse=True)
def frozen_now(monkeypatch):
    monkeypatch.setattr(task_service, "_now", lambda: NOW)


@pytest.fixture()
def wire(monkeypatch):
    """默认：权限通过、绑定可用、知识可读、审计吞掉。"""
    state = {
        "allowed": True,
        "binding": (True, IdentityBinding(identity_state=IdentityState.OK, owner_user_id=501)),
        "audits": [],
        "knowledge_calls": 0,
    }

    async def _allowed(login_user, object_type, object_id, permission_ids):
        return state["allowed"]

    async def _binding(**kwargs):
        return state["binding"]

    async def _knowledge(**kwargs):
        state["knowledge_calls"] += 1

    async def _audit(user, action, task, **metadata):
        state["audits"].append((action.value, task.id, metadata))

    from bisheng.permission.domain.services.application_permission_service import (
        ApplicationPermissionService,
    )

    # 形状校验会读 Flow.data（真库）——这里给"查不到流程"的桩，让它走"应用不存在→跳过
    # 形状判定"的分支；`assert_app_schedulable` 本身由 test_nl_draft.py 专测。
    async def _no_flow(flow_id):
        return None

    monkeypatch.setattr(app_shape_guard.FlowDao, "aget_flow_by_id", _no_flow)
    monkeypatch.setattr(ApplicationPermissionService, "has_any_permission_async", _allowed)
    monkeypatch.setattr(trigger_guard, "validate_binding", _binding)
    monkeypatch.setattr(knowledge_guard, "assert_app_knowledge_readable", _knowledge)
    monkeypatch.setattr(task_service, "_audit", _audit)
    return state


def _req(**overrides) -> ScheduledTaskCreateReq:
    base = {
        "name": "nightly",
        "app_id": "1001",
        "app_type": FlowType.WORKFLOW.value,
        "schedule_type": "cron",
        "cron_expression": "0 9 * * *",
        "service_account_id": 5,
    }
    base.update(overrides)
    return ScheduledTaskCreateReq(**base)


async def test_create_locks_owner_and_computes_next_run(patched_db, wire):
    task = await task_service.create_task(_User(), _req())

    assert task.run_identity_user_id == 501, "创建通过后锁定该账号的 owner（INV-25）"
    assert task.status == TaskStatus.ENABLED.value
    assert task.cron_expression == "0 9 * * *"
    assert task.next_run_at.replace(tzinfo=None) == datetime(2026, 10, 17, 9, 0)
    assert wire["knowledge_calls"] == 1, "创建期必须过知识可见性校验（AC-29）"
    assert wire["audits"][0][0] == "schedule.task.create"


async def test_create_rejects_without_edit_app(patched_db, wire):
    wire["allowed"] = False
    with pytest.raises(UnAuthorizedError):
        await task_service.create_task(_User(), _req())


async def test_create_rejects_app_and_identity_errors_distinctly(patched_db, wire):
    wire["binding"] = (False, IdentityBinding(identity_state=IdentityState.ACCOUNT_UNAVAILABLE))
    with pytest.raises(ScheduledTaskConfigInvalidError):
        await task_service.create_task(_User(), _req())

    wire["binding"] = (True, IdentityBinding(identity_state=IdentityState.NO_USE_APP))
    with pytest.raises(ScheduledTaskIdentityUnavailableError):
        await task_service.create_task(_User(), _req())


async def test_create_enforces_per_app_limit(patched_db, wire, monkeypatch):
    monkeypatch.setattr(task_service._conf(), "max_tasks_per_app", 1)
    await task_service.create_task(_User(), _req())
    with pytest.raises(ScheduledTaskLimitExceededError):
        await task_service.create_task(_User(), _req(name="second"))


async def test_create_validates_schedule_forms(patched_db, wire):
    with pytest.raises(ScheduledTaskCronInvalidError):
        await task_service.create_task(_User(), _req(cron_expression="@daily"))

    with pytest.raises(ScheduledTaskConfigInvalidError):
        await task_service.create_task(_User(), _req(schedule_type="once"))  # 缺 run_at

    once = await task_service.create_task(
        _User(), _req(schedule_type="once", cron_expression=None, run_at=NOW + timedelta(hours=2))
    )
    assert once.cron_expression is None
    assert once.next_run_at.replace(tzinfo=None) == NOW + timedelta(hours=2)


async def test_create_validates_preset_inputs(patched_db, wire, monkeypatch):
    monkeypatch.setattr(task_service._conf(), "preset_input_max_items", 1)
    with pytest.raises(ScheduledTaskConfigInvalidError):
        await task_service.create_task(
            _User(), _req(preset_inputs=[PresetInputItem(key="a", value="1"), PresetInputItem(key="b", value="2")])
        )


async def test_admin_bypasses_edit_app(patched_db, wire):
    wire["allowed"] = False
    task = await task_service.create_task(_User(tenant_admin=True), _req())
    assert task.id is not None, "租户管理员走显式放行（AC-26）"


async def test_update_reschedule_and_identity_change(patched_db, wire):
    task = await task_service.create_task(_User(), _req())

    updated = await task_service.update_task(_User(), task.id, ScheduledTaskUpdateReq(cron_expression="30 8 * * *"))
    assert updated.cron_expression == "30 8 * * *"
    assert updated.next_run_at.replace(tzinfo=None) == datetime(2026, 10, 17, 8, 30)

    wire["binding"] = (True, IdentityBinding(identity_state=IdentityState.OK, owner_user_id=777))
    changed = await task_service.update_task(_User(), task.id, ScheduledTaskUpdateReq(service_account_id=9))
    assert changed.service_account_id == 9
    assert changed.run_identity_user_id == 777, "换运行身份时同步更新锁定的 owner（AC-03）"

    failed = await task_service.update_task(_User(), task.id, ScheduledTaskUpdateReq(name="renamed"))
    assert failed.name == "renamed"
    assert any(a[0] == "schedule.task.update" for a in wire["audits"])


async def test_audit_snapshots_cover_crud_and_hide_preset_values(patched_db, wire):
    """AC-49/AC-51：管理动作带关键前后取值，但 preset 的 value 绝不进审计。"""
    task = await task_service.create_task(_User(), _req())
    create_audit = next(a for a in wire["audits"] if a[0] == "schedule.task.create")
    assert create_audit[2]["after"]["name"] == "nightly"
    assert create_audit[2]["after"]["cron_expression"] == "0 9 * * *"

    secret = "TOP-SECRET-PRESET-VALUE"
    await task_service.update_task(
        _User(),
        task.id,
        ScheduledTaskUpdateReq(
            name="renamed",
            preset_inputs=[PresetInputItem(key="question", value=secret)],
        ),
    )
    update_audit = next(a for a in wire["audits"] if a[0] == "schedule.task.update")
    assert update_audit[2]["before"]["name"] == "nightly"
    assert update_audit[2]["after"]["name"] == "renamed"
    assert update_audit[2]["after"]["preset_keys"] == ["question"]
    assert update_audit[2]["preset_values_changed"] is True
    assert secret not in str(update_audit[2]), "preset 值属于输入正文，不得进入审计"

    # 只改 value（key 不变）：before/after 的 preset_keys 相同，flag 必须仍能标记变化。
    await task_service.update_task(
        _User(),
        task.id,
        ScheduledTaskUpdateReq(preset_inputs=[PresetInputItem(key="question", value="v2")]),
    )
    value_audit = next(a for a in reversed(wire["audits"]) if a[0] == "schedule.task.update")
    assert value_audit[2]["preset_values_changed"] is True
    assert value_audit[2]["before"]["preset_keys"] == value_audit[2]["after"]["preset_keys"] == ["question"]
    assert "v2" not in str(value_audit[2])

    await task_service.set_enabled(_User(), task.id, enabled=False)
    disable_audit = next(a for a in wire["audits"] if a[0] == "schedule.task.disable")
    assert disable_audit[2]["before"] == {"status": "enabled"}
    assert disable_audit[2]["after"] == {"status": "disabled"}

    await task_service.delete_task(_User(), task.id)
    delete_audit = next(a for a in wire["audits"] if a[0] == "schedule.task.delete")
    assert delete_audit[2]["before"]["name"] == "renamed"


async def test_enable_recomputes_next_run_disable_keeps_it(patched_db, wire):
    task = await task_service.create_task(_User(), _req())

    disabled = await task_service.set_enabled(_User(), task.id, enabled=False)
    assert disabled.status == TaskStatus.DISABLED.value

    enabled = await task_service.set_enabled(_User(), task.id, enabled=True)
    assert enabled.status == TaskStatus.ENABLED.value
    assert enabled.next_run_at.replace(tzinfo=None) == datetime(2026, 10, 17, 9, 0), (
        "AC-02：重新启用自下一个计划时刻起恢复，停用期间的到期轮不补跑"
    )


async def test_delete_and_missing_task(patched_db, wire):
    task = await task_service.create_task(_User(), _req())
    await task_service.delete_task(_User(), task.id)

    async with AsyncSession(bind=patched_db) as session:
        rows = (await session.execute(select(ScheduledTask))).scalars().all()
    assert rows == []

    with pytest.raises(NotFoundError):
        await task_service.delete_task(_User(), task.id)


async def test_list_requires_app_or_admin(patched_db, wire):
    await task_service.create_task(_User(), _req())

    with pytest.raises(UnAuthorizedError):
        await task_service.list_tasks(
            _User(), app_id=None, app_type=None, status=None, keyword=None, page=1, page_size=20
        )

    rows, total = await task_service.list_tasks(
        _User(is_global_super=True), app_id=None, app_type=None, status=None, keyword=None, page=1, page_size=20
    )
    assert total == 1 and rows[0].name == "nightly"

    rows, total = await task_service.list_tasks(
        _User(), app_id="1001", app_type=FlowType.WORKFLOW.value, status=None, keyword=None, page=1, page_size=20
    )
    assert total == 1


def test_preview_returns_description_and_next_runs():
    expression, description, display, runs = task_service.preview_schedule("0 9 * * mon-fri")
    assert expression == "0 9 * * mon-fri"
    assert "9:00" in description
    assert display["day"] == {"kind": "weekdays", "weekdays": [1, 2, 3, 4, 5], "range": True}
    assert display["time"] == {"kind": "at", "times": [{"hour": 9, "minute": 0}]}
    assert len(runs) == 5


def test_assert_app_deletable(monkeypatch):
    """AC-30：启用中的任务阻断删除；停用/历史不阻断。"""
    from bisheng.common.errcode.scheduled_task import ScheduledTaskAppInUseError

    monkeypatch.setattr(ScheduledTaskDao, "count_enabled_by_app_sync", lambda app_id, app_type: 0)
    task_service.assert_app_deletable("1001", FlowType.WORKFLOW.value)  # 不抛

    monkeypatch.setattr(ScheduledTaskDao, "count_enabled_by_app_sync", lambda app_id, app_type: 2)
    with pytest.raises(ScheduledTaskAppInUseError) as excinfo:
        task_service.assert_app_deletable("1001", FlowType.WORKFLOW.value)
    assert excinfo.value.Code == 11908
    assert excinfo.value.kwargs["count"] == 2
