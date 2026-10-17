"""Access denial and memory integration boundaries for F074."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from bisheng.common.errcode.http_error import UnAuthorizedError
from bisheng.core.context.tenant import current_tenant_id, set_current_tenant_id
from bisheng.linsight.domain.services.agent_config_service import AgentConfigService
from bisheng.linsight.domain.services.memory_adapter import UnavailableMemoryAdapter, memory_bindings


async def test_private_session_requires_both_owner_and_tenant():
    token = set_current_tenant_id(12)
    try:
        task = SimpleNamespace(user_id=8, tenant_id=12)
        service = AgentConfigService(repository=SimpleNamespace(task=AsyncMock(return_value=task)), store=object())
        assert await service.owned_task("task", SimpleNamespace(user_id=8)) is task
        with pytest.raises(UnAuthorizedError):
            await service.owned_task("task", SimpleNamespace(user_id=9))
        task.tenant_id = 13
        with pytest.raises(UnAuthorizedError):
            await service.owned_task("task", SimpleNamespace(user_id=8))
    finally:
        current_tenant_id.reset(token)


async def test_unavailable_memory_does_not_claim_to_save():
    adapter = UnavailableMemoryAdapter()
    assert await memory_bindings(adapter, object()) == ("", [])
    assert (await adapter.submit_candidates(None, "task", ["example"]))["saved"] is False


async def test_memory_tools_use_server_scope_and_task_source():
    adapter = SimpleNamespace(
        available=True,
        load_context=AsyncMock(return_value="Prefer concise reports"),
        submit_candidates=AsyncMock(return_value={"status": "proposed"}),
    )
    session = SimpleNamespace(tenant_id=12, user_id=8, id="task", question="Research")
    context, tools = await memory_bindings(adapter, session)
    assert context == "Prefer concise reports"
    result = await tools[0].ainvoke({"experience": "Normalize units first"})
    assert result["status"] == "proposed"
    scope, task_id, candidates = adapter.submit_candidates.call_args.args
    assert (scope.tenant_id, scope.user_id, task_id) == (12, 8, "task")
    assert candidates == ["Normalize units first"]
