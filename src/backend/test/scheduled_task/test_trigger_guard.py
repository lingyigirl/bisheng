"""F077 T-09：触发前复检（trigger_guard）。

覆盖 spec AC-16/AC-21/AC-22/AC-23 的判定面：应用存在性与上线、运行身份可用性
（账号停用 / 换 owner / owner 停用 / owner 被提权 / 无 use_app），以及一条**反向约束**——
复检失败时**不得回带任何可用的执行身份**（不得回退为其他身份，AC-22）。

四个协作者全部 mock（本模块只判定、不写库，也不碰真实 DB/权限服务）。
"""

import pytest

from bisheng.database.models.assistant import AssistantStatus
from bisheng.database.models.flow import FlowStatus, FlowType
from bisheng.scheduled_task.domain.models.scheduled_task import ScheduledTask
from bisheng.scheduled_task.domain.services import trigger_guard
from bisheng.scheduled_task.domain.services.trigger_guard import (
    AppState,
    IdentityState,
    describe_failure,
    recheck_task,
)

TENANT_ID = 7


def _task(app_type: int = FlowType.WORKFLOW.value) -> ScheduledTask:
    return ScheduledTask(
        id=1,
        tenant_id=TENANT_ID,
        name="nightly",
        app_id="1001",
        app_type=app_type,
        service_account_id=5,
        run_identity_user_id=501,
        created_by=42,
    )


class _App:
    def __init__(self, status: int, *, is_delete: int = 0, flow_type: int = FlowType.WORKFLOW.value):
        self.status = status
        self.is_delete = is_delete
        self.flow_type = flow_type


class _Account:
    def __init__(self, *, owner_user_id: int = 501, enabled: bool = True):
        self.resource_owner_user_id = owner_user_id
        self.is_enabled = enabled


@pytest.fixture()
def wire(monkeypatch):
    """默认全部通过；每个用例只改自己要考的那一项。"""
    state = {
        "workflow": _App(FlowStatus.ONLINE.value),
        "assistant": _App(AssistantStatus.ONLINE.value),
        "account": _Account(),
        "payload": object(),
        "privileged": False,
        "use_app": True,
    }

    async def _aget_flow(flow_id):
        return state["workflow"]

    async def _aget_assistant(assistant_id):
        return state["assistant"]

    async def _aget_scoped(account_id, *, tenant_id):
        return state["account"]

    async def _load_owner(*, owner_user_id, tenant_id):
        return state["payload"]

    async def _is_privileged(payload, tenant_id):
        return state["privileged"]

    async def _has_use_app(login_user, object_type, object_id, permission_ids):
        assert object_type in ("assistant", "workflow")
        return state["use_app"]

    monkeypatch.setattr(trigger_guard.FlowDao, "aget_flow_by_id", _aget_flow)
    monkeypatch.setattr(trigger_guard.AssistantDao, "aget_one_assistant", _aget_assistant)
    monkeypatch.setattr(trigger_guard.ServiceAccountDao, "aget_scoped", _aget_scoped)
    monkeypatch.setattr(trigger_guard, "load_owner_payload", _load_owner)
    monkeypatch.setattr(trigger_guard, "owner_is_privileged", _is_privileged)
    monkeypatch.setattr(trigger_guard.ApplicationPermissionService, "has_any_permission_async", _has_use_app)
    return state


async def test_all_green_returns_payload(wire):
    outcome = await recheck_task(_task())
    assert outcome.ok
    assert outcome.app_state is AppState.OK and outcome.identity_state is IdentityState.OK
    assert outcome.payload is wire["payload"], "复检通过时回带身份，供派发方直接使用"


async def test_app_offline(wire):
    wire["workflow"] = _App(FlowStatus.OFFLINE.value)
    outcome = await recheck_task(_task())
    assert outcome.app_state is AppState.OFFLINE and not outcome.app_ok
    assert outcome.identity_ok, "应用下线与身份两件事分开报——处置也不同（跳过 vs 记失败）"


async def test_app_missing_or_wrong_type_is_deleted(wire):
    wire["workflow"] = None
    assert (await recheck_task(_task())).app_state is AppState.DELETED

    wire["workflow"] = _App(FlowStatus.ONLINE.value, flow_type=FlowType.ASSISTANT.value)
    assert (await recheck_task(_task())).app_state is AppState.DELETED


async def test_assistant_soft_deleted_is_deleted(wire):
    wire["assistant"] = _App(AssistantStatus.ONLINE.value, is_delete=1)
    outcome = await recheck_task(_task(app_type=FlowType.ASSISTANT.value))
    assert outcome.app_state is AppState.DELETED


async def test_unsupported_app_type_is_deleted(wire):
    outcome = await recheck_task(_task(app_type=99))
    assert outcome.app_state is AppState.DELETED


async def test_account_disabled_and_owner_changed(wire):
    wire["account"] = _Account(enabled=False)
    outcome = await recheck_task(_task())
    assert outcome.identity_state is IdentityState.ACCOUNT_UNAVAILABLE
    assert outcome.payload is None, "身份不可用时绝不回带任何可执行身份（AC-22）"

    wire["account"] = _Account(owner_user_id=999)
    outcome = await recheck_task(_task())
    assert outcome.identity_state is IdentityState.OWNER_CHANGED
    assert outcome.payload is None


async def test_owner_unavailable_and_privileged(wire):
    wire["payload"] = None
    outcome = await recheck_task(_task())
    assert outcome.identity_state is IdentityState.OWNER_UNAVAILABLE
    assert outcome.payload is None

    wire["payload"] = object()
    wire["privileged"] = True
    outcome = await recheck_task(_task())
    assert outcome.identity_state is IdentityState.OWNER_PRIVILEGED
    assert outcome.payload is None


async def test_use_app_revoked(wire):
    wire["use_app"] = False
    outcome = await recheck_task(_task())
    assert outcome.identity_state is IdentityState.NO_USE_APP
    assert outcome.payload is None


async def test_describe_failure_is_a_short_marker(wire):
    wire["workflow"] = _App(FlowStatus.OFFLINE.value)
    wire["account"] = _Account(enabled=False)
    outcome = await recheck_task(_task())
    assert describe_failure(outcome) == "app_offline,account_unavailable"

    wire["workflow"] = _App(FlowStatus.ONLINE.value)
    wire["account"] = _Account()
    assert describe_failure(await recheck_task(_task())) == "", "通过时无失败原因"
