"""F078 T-21: scheduling guards for app_type=100."""

from types import SimpleNamespace

import pytest

from bisheng.common.errcode.managed_agent import ManagedAgentConfigInvalidError
from bisheng.common.errcode.scheduled_task import ScheduledTaskAppNotSchedulableError
from bisheng.managed_agent.domain.constants import MANAGED_AGENT_APP_TYPE
from bisheng.managed_agent.domain.models.dao import ManagedAgentDao
from bisheng.scheduled_task.domain.services import app_shape_guard, knowledge_guard, task_service


def _agent(card=None, source_kind="high_code", native_app_type=None):
    return SimpleNamespace(
        id=1,
        source_kind=source_kind,
        native_app_type=native_app_type,
        native_app_id="flow-1" if native_app_type else None,
        agent_card=card,
    )


def _card(*inputs):
    return {"inputs": list(inputs)}


async def test_external_file_input_is_not_schedulable(monkeypatch):
    async def _aget(agent_id):
        return _agent(_card({"key": "file", "type": "string", "file": True}))

    monkeypatch.setattr(ManagedAgentDao, "aget", _aget)
    with pytest.raises(ScheduledTaskAppNotSchedulableError):
        await app_shape_guard.assert_app_schedulable("1", MANAGED_AGENT_APP_TYPE)


async def test_external_text_input_is_schedulable(monkeypatch):
    async def _aget(agent_id):
        return _agent(_card({"key": "query", "type": "string"}))

    monkeypatch.setattr(ManagedAgentDao, "aget", _aget)
    await app_shape_guard.assert_app_schedulable("1", MANAGED_AGENT_APP_TYPE)


async def test_third_party_has_no_knowledge_refs(monkeypatch):
    async def _aget(agent_id):
        return _agent()

    monkeypatch.setattr(ManagedAgentDao, "aget", _aget)
    assert await knowledge_guard.collect_app_refs("1", MANAGED_AGENT_APP_TYPE) == []


async def test_preset_validation_uses_agent_card(monkeypatch):
    async def _aget(agent_id):
        return _agent(_card({"key": "query", "type": "string", "required": True}))

    monkeypatch.setattr(ManagedAgentDao, "aget", _aget)
    normalized = await task_service._validate_presets_for_app(
        "1", MANAGED_AGENT_APP_TYPE, [{"key": "query", "value": "hi"}]
    )
    assert normalized == [{"key": "query", "value": "hi"}]

    with pytest.raises(ManagedAgentConfigInvalidError):
        await task_service._validate_presets_for_app("1", MANAGED_AGENT_APP_TYPE, [{"key": "unknown", "value": "x"}])


async def test_preset_validation_rejects_file_input(monkeypatch):
    async def _aget(agent_id):
        return _agent(_card({"key": "file", "type": "string", "file": True}))

    monkeypatch.setattr(ManagedAgentDao, "aget", _aget)
    with pytest.raises(ScheduledTaskAppNotSchedulableError):
        await task_service._validate_presets_for_app("1", MANAGED_AGENT_APP_TYPE, [{"key": "file", "value": "x"}])
