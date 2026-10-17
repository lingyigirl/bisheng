"""Check resource loss and snapshot drift without an external database."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from bisheng.common.errcode.linsight import LinsightAgentConfigurationError
from bisheng.linsight.domain.schemas.agent_config import AgentConfig
from bisheng.linsight.domain.services.agent_config_service import AgentConfigService


def config(**changes):
    return AgentConfig(subagents=[{"id": "research", "name": "Research", "description": "Evidence", **changes}])


@pytest.fixture
def service(monkeypatch):
    repository = SimpleNamespace(
        enabled_skills=AsyncMock(return_value=[]),
        tools=AsyncMock(return_value=[]),
        tool_types=AsyncMock(return_value=[]),
    )
    monkeypatch.setattr(
        "bisheng.linsight.domain.services.agent_config_service.LLMService.get_model_for_call",
        AsyncMock(return_value=SimpleNamespace(online=True, model_type="llm")),
    )
    return AgentConfigService(repository=repository, store=MagicMock())


async def test_missing_tool_is_rejected_instead_of_silently_filtered(service):
    with pytest.raises(LinsightAgentConfigurationError):
        await service.resolve(config(tool_refs=[42]), SimpleNamespace(user_id=1), 1, "7")


async def test_default_model_is_frozen_and_revalidation_detects_skill_drift(service):
    snapshot = await service.resolve(config(), SimpleNamespace(user_id=1), 1, "7")
    assert snapshot["subagents"][0]["model_id"] == "7"
    assert snapshot["event_protocol"] == 2
    snapshot["skill_digests"] = {"research": {"old": "digest"}}
    with pytest.raises(LinsightAgentConfigurationError):
        await service.revalidate(snapshot, SimpleNamespace(user_id=1), 1)


async def test_disabled_skill_is_rejected(service):
    with pytest.raises(LinsightAgentConfigurationError):
        await service.resolve(config(skill_names=["disabled"]), SimpleNamespace(user_id=1), 1, "7")


async def test_existing_but_unauthorized_connector_is_rejected(service, monkeypatch):
    service.repository.tools.return_value = [SimpleNamespace(id=42, type=8, is_delete=0)]
    service.repository.tool_types.return_value = [SimpleNamespace(id=8, is_delete=0, is_preset=2)]
    permission = AsyncMock(return_value=False)
    monkeypatch.setattr(
        "bisheng.linsight.domain.services.agent_config_service.ToolPermissionService.has_any_permission_async",
        permission,
    )
    with pytest.raises(LinsightAgentConfigurationError):
        await service.resolve(config(connector_refs=[42]), SimpleNamespace(user_id=1), 1, "7")
    permission.assert_awaited_once()


async def test_runtime_revocation_prevents_tool_execution(service):
    from bisheng.linsight.domain.services.configured_subagents import MemberResourceGuard

    guard = MemberResourceGuard(
        config(tool_refs=[42], model_id="7").subagents[0].model_dump(), SimpleNamespace(user_id=1), 1
    )
    guard.service = service
    handler = AsyncMock()
    with pytest.raises(LinsightAgentConfigurationError):
        await guard.awrap_tool_call(object(), handler)
    handler.assert_not_awaited()
