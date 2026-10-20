"""F078 T-20: app_type=100 branches in F077's trigger guard."""

from types import SimpleNamespace

from bisheng.managed_agent.domain.constants import MANAGED_AGENT_APP_TYPE
from bisheng.managed_agent.domain.models.dao import ManagedAgentDao
from bisheng.scheduled_task.domain.services import trigger_guard

TASK = SimpleNamespace(
    app_type=MANAGED_AGENT_APP_TYPE,
    app_id="1",
    tenant_id=7,
    service_account_id=5,
    run_identity_user_id=42,
)


def _agent(status="online", source_kind="high_code", native_app_type=None):
    return SimpleNamespace(
        id=1,
        status=status,
        source_kind=source_kind,
        native_app_type=native_app_type,
        native_app_id="flow-1" if native_app_type else None,
    )


def test_object_type_mapping():
    assert trigger_guard.object_type_of(MANAGED_AGENT_APP_TYPE) == "managed_agent"


async def test_app_state_online_external_is_ok(monkeypatch):
    async def _aget(agent_id):
        return _agent()

    monkeypatch.setattr(ManagedAgentDao, "aget", _aget)
    assert await trigger_guard._app_state(TASK) is trigger_guard.AppState.OK


async def test_app_state_offline_is_offline(monkeypatch):
    async def _aget(agent_id):
        return _agent(status="offline")

    monkeypatch.setattr(ManagedAgentDao, "aget", _aget)
    assert await trigger_guard._app_state(TASK) is trigger_guard.AppState.OFFLINE


async def test_app_state_missing_is_deleted(monkeypatch):
    async def _aget(agent_id):
        return None

    monkeypatch.setattr(ManagedAgentDao, "aget", _aget)
    assert await trigger_guard._app_state(TASK) is trigger_guard.AppState.DELETED


async def test_native_projection_unavailable_maps_to_offline(monkeypatch):
    async def _aget(agent_id):
        return _agent(source_kind="native", native_app_type=10)

    async def _native_state(agent):
        return trigger_guard.AppState.OFFLINE

    monkeypatch.setattr(ManagedAgentDao, "aget", _aget)
    monkeypatch.setattr(trigger_guard, "_native_projection_state", _native_state)
    assert await trigger_guard._app_state(TASK) is trigger_guard.AppState.OFFLINE


async def test_app_exists_requires_online(monkeypatch):
    async def _online(agent_id):
        return _agent(status="online")

    monkeypatch.setattr(ManagedAgentDao, "aget", _online)
    assert await trigger_guard._app_exists("1", MANAGED_AGENT_APP_TYPE) is True

    async def _offline(agent_id):
        return _agent(status="offline")

    monkeypatch.setattr(ManagedAgentDao, "aget", _offline)
    assert await trigger_guard._app_exists("1", MANAGED_AGENT_APP_TYPE) is False
