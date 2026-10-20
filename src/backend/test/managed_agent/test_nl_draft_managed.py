"""F078 T-23: NL draft targets managed agents and validates the Agent Card."""

import json
from types import SimpleNamespace

import pytest

from bisheng.common.errcode.scheduled_task import ScheduledTaskNlDraftInvalidError
from bisheng.managed_agent.domain.constants import MANAGED_AGENT_APP_TYPE
from bisheng.managed_agent.domain.models.dao import ManagedAgentDao
from bisheng.scheduled_task.domain.services import nl_draft_service


class _Reply:
    def __init__(self, content):
        self.content = content


class _Model:
    def __init__(self, content):
        self.content = content

    async def ainvoke(self, prompt):
        return _Reply(self.content)


@pytest.fixture()
def model(monkeypatch):
    state = {"content": "{}"}

    async def _resolve(tenant_id, user_id):
        return _Model(state["content"])

    monkeypatch.setattr(nl_draft_service, "_resolve_model", _resolve)
    return state


def _agent(card):
    return SimpleNamespace(id=1, source_kind="high_code", agent_card=card)


async def test_draft_validates_presets_against_card(model, monkeypatch):
    async def _aget(agent_id):
        return _agent({"inputs": [{"key": "query", "type": "string", "required": True}]})

    monkeypatch.setattr(ManagedAgentDao, "aget", _aget)
    model["content"] = json.dumps(
        {
            "schedule_type": "cron",
            "cron_expression": "0 9 * * *",
            "preset_inputs": [{"key": "query", "value": "hi"}],
        }
    )
    draft = await nl_draft_service.draft_from_text(
        tenant_id=7,
        user_id=42,
        text="每天九点",
        app_id="1",
        app_type=MANAGED_AGENT_APP_TYPE,
    )
    assert draft["preset_inputs"] == [{"key": "query", "value": "hi"}]


async def test_missing_required_preset_rejects_draft(model, monkeypatch):
    async def _aget(agent_id):
        return _agent({"inputs": [{"key": "query", "type": "string", "required": True}]})

    monkeypatch.setattr(ManagedAgentDao, "aget", _aget)
    model["content"] = json.dumps({"schedule_type": "cron", "cron_expression": "0 9 * * *"})
    with pytest.raises(ScheduledTaskNlDraftInvalidError):
        await nl_draft_service.draft_from_text(
            tenant_id=7,
            user_id=42,
            text="每天九点",
            app_id="1",
            app_type=MANAGED_AGENT_APP_TYPE,
        )


async def test_file_input_rejects_draft(model, monkeypatch):
    async def _aget(agent_id):
        return _agent({"inputs": [{"key": "file", "type": "string", "file": True}]})

    monkeypatch.setattr(ManagedAgentDao, "aget", _aget)
    model["content"] = json.dumps(
        {
            "schedule_type": "cron",
            "cron_expression": "0 9 * * *",
            "preset_inputs": [{"key": "file", "value": "x"}],
        }
    )
    with pytest.raises(ScheduledTaskNlDraftInvalidError):
        await nl_draft_service.draft_from_text(
            tenant_id=7,
            user_id=42,
            text="每天九点",
            app_id="1",
            app_type=MANAGED_AGENT_APP_TYPE,
        )
