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
        runtime_tools=AsyncMock(return_value=[]),
        enabled_skill_names=AsyncMock(return_value=set()),
    )
    monkeypatch.setattr(
        "bisheng.linsight.domain.services.agent_config_service.LLMService.get_model_for_call",
        AsyncMock(return_value=SimpleNamespace(online=True, model_type="llm")),
    )
    return AgentConfigService(repository=repository, store=MagicMock())


async def test_missing_tool_is_rejected_instead_of_silently_filtered(service):
    with pytest.raises(LinsightAgentConfigurationError):
        await service.resolve(config(tool_refs=[42]), SimpleNamespace(user_id=1), 1, "7")


@pytest.mark.parametrize("empty", [None, AgentConfig()])
async def test_new_task_without_members_still_freezes_harness_protocol(service, empty):
    snapshot = await service.resolve(empty, SimpleNamespace(user_id=1), 1, "7")
    assert snapshot == {
        "schema_version": 1,
        "subagents": [],
        "main_model_id": "7",
        "event_protocol": 2,
        "recovery_protocol": 1,
        "skill_digests": {},
    }
    await service.revalidate(snapshot, SimpleNamespace(user_id=1), 1)
    service.repository.enabled_skills.assert_not_awaited()
    service.repository.tools.assert_not_awaited()
    service.repository.tool_types.assert_not_awaited()


async def test_no_member_task_uses_tenant_default_model(service, monkeypatch):
    default = AsyncMock(return_value=SimpleNamespace(linsight_default_model_id="9"))
    monkeypatch.setattr("bisheng.linsight.domain.services.agent_config_service.LLMService.get_workbench_llm", default)
    snapshot = await service.resolve(None, SimpleNamespace(user_id=1), 1, None)
    assert snapshot["main_model_id"] == "9"
    default.assert_awaited_once_with(tenant_id=1)


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


async def test_runtime_permissions_are_batched_by_group_and_refreshed_every_call(service, monkeypatch):
    service.repository.runtime_tools.return_value = [
        SimpleNamespace(id=i, type=8, is_delete=0, group_deleted=0, is_preset=2) for i in range(1, 51)
    ]
    permission = AsyncMock(side_effect=[{"8": {"use_tool"}}, {"8": set()}])
    monkeypatch.setattr(
        "bisheng.linsight.domain.services.agent_config_service.ToolPermissionService.get_tool_permission_map_async",
        permission,
    )
    member = config(connector_refs=list(range(1, 51)), model_id="7").subagents[0]
    user = SimpleNamespace(user_id=1)
    await service.validate_runtime_member(member, user)
    with pytest.raises(LinsightAgentConfigurationError):
        await service.validate_runtime_member(member, user)
    assert permission.await_count == 2
    permission.assert_awaited_with(user, ["8"], ["use_tool"])
    assert service.repository.runtime_tools.await_count == 2


@pytest.mark.parametrize("resource", ["skill", "model", "group", "tool"])
async def test_runtime_resource_revocation_is_rechecked(service, monkeypatch, resource):
    model = SimpleNamespace(online=True, model_type="llm")
    monkeypatch.setattr(
        "bisheng.linsight.domain.services.agent_config_service.LLMService.get_model_for_call",
        AsyncMock(return_value=model),
    )
    row = SimpleNamespace(id=42, type=8, is_delete=0, group_deleted=0, is_preset=0)
    # Preset value is supplied by the same enum as production.
    from bisheng.tool.domain.const import ToolPresetType

    row.is_preset = ToolPresetType.PRESET.value
    service.repository.runtime_tools.return_value = [row]
    service.repository.enabled_skill_names.return_value = {"selected"}
    member = config(tool_refs=[42], skill_names=["selected"], model_id="7").subagents[0]
    await service.validate_runtime_member(member, SimpleNamespace(user_id=1))
    if resource == "skill":
        service.repository.enabled_skill_names.return_value = set()
    elif resource == "model":
        model.online = False
    elif resource == "group":
        row.group_deleted = 1
    else:
        row.is_delete = 1
    with pytest.raises(LinsightAgentConfigurationError):
        await service.validate_runtime_member(member, SimpleNamespace(user_id=1))
