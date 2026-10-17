"""Verify independent member assembly against the real factory adapter."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from bisheng.common.errcode.linsight import LinsightAgentConfigurationError
from bisheng.linsight.domain.services.configured_subagents import build_configured_subagents


async def test_members_resolve_separate_models_and_explicit_tools(monkeypatch):
    from bisheng.linsight.domain.services import agent_factory, configured_subagents
    from bisheng.linsight.domain.services.workbench_impl import LinsightWorkbenchImpl

    models = AsyncMock(side_effect=lambda session, model_id: f"model-{model_id}")
    monkeypatch.setattr(agent_factory, "_resolve_model", models)
    monkeypatch.setattr(configured_subagents.AgentConfigService, "revalidate", AsyncMock())
    tool = SimpleNamespace(name="search", tool_id=7)
    initialize = AsyncMock(return_value=[tool])
    monkeypatch.setattr(LinsightWorkbenchImpl, "init_linsight_config_tools", initialize)
    common = {"description": "Research", "system_prompt": "Return evidence", "skill_names": [], "connector_refs": []}
    members = [
        {**common, "id": "a", "name": "A", "model_id": "11", "tool_refs": [7]},
        {**common, "id": "b", "name": "B", "model_id": "12", "tool_refs": []},
    ]
    session = SimpleNamespace(
        agent_config={"subagents": members},
        user_id=1,
        tenant_id=17,
        model_copy=lambda update: SimpleNamespace(**update),
    )
    resources = []
    specs, middleware = await build_configured_subagents(session, object(), "/tmp", lambda tools: [], resources)
    assert [spec["model"] for spec in specs] == ["model-11", "model-12"]
    assert [spec["tools"] for spec in specs] == [[tool], []]
    # Every member owns its ``member-<id>`` slot — member #0 must NOT shadow the
    # built-in researcher's ``general-purpose`` name (2026-10-16 fix: a
    # tool-less first member silently removed the researcher from the graph).
    assert [spec["name"] for spec in specs] == ["member-a", "member-b"]
    assert len(middleware.members) == 2
    assert resources == [tool]
    assert specs[0]["middleware"][1].user.tenant_id == 17
    initialize.assert_awaited_once()
    # T1 (2026-10-16): every collaborator prompt carries the one-line artifact
    # report contract (the single channel for artifact awareness).
    from bisheng.linsight.domain.services.agent_factory import SUBAGENT_ARTIFACT_REPORT_CONTRACT

    for spec in specs:
        assert spec["system_prompt"].endswith(SUBAGENT_ARTIFACT_REPORT_CONTRACT)


def test_harness_never_falls_back_to_host_code_execution():
    from bisheng.linsight.domain.services.configured_subagents import bind_cloud_workspace

    with pytest.raises(LinsightAgentConfigurationError):
        bind_cloud_workspace([SimpleNamespace(name="bisheng_code_interpreter", executor=object())], object())


@pytest.mark.parametrize("wrapped", [False, True])
def test_cloud_executor_binds_workspace_through_tool_wrapper(wrapped):
    from bisheng.linsight.domain.services.configured_subagents import bind_cloud_workspace
    from bisheng_langchain.gpts.tools.code_interpreter.e2b_executor import E2bCodeExecutor

    executor = object.__new__(E2bCodeExecutor)
    code_tool = SimpleNamespace(name="bisheng_code_interpreter", executor=executor)
    tool = SimpleNamespace(name=code_tool.name, tool_instance=code_tool) if wrapped else code_tool
    workspace = object()
    bind_cloud_workspace([tool], workspace)
    assert executor.workspace_backend is workspace


def test_wrapped_local_executor_is_rejected():
    from bisheng.linsight.domain.services.configured_subagents import bind_cloud_workspace

    tool = SimpleNamespace(name="bisheng_code_interpreter", tool_instance=SimpleNamespace(executor=object()))
    with pytest.raises(LinsightAgentConfigurationError):
        bind_cloud_workspace([tool], object())


def test_main_graph_binding_skips_non_e2b_executor_when_not_required():
    """require_e2b=False (main graph): a local executor is skipped, not rejected.

    Only E2B has a copy-out path that consumes the workspace backend; local
    deployments keep their previous behavior instead of failing the task.
    """
    from bisheng.linsight.domain.services.configured_subagents import bind_cloud_workspace

    executor = SimpleNamespace()
    tool = SimpleNamespace(name="bisheng_code_interpreter", tool_instance=SimpleNamespace(executor=executor))
    bind_cloud_workspace([tool], object(), require_e2b=False)
    assert not hasattr(executor, "workspace_backend")


def test_main_graph_binding_still_binds_e2b_executor():
    from bisheng.linsight.domain.services.configured_subagents import bind_cloud_workspace
    from bisheng_langchain.gpts.tools.code_interpreter.e2b_executor import E2bCodeExecutor

    executor = object.__new__(E2bCodeExecutor)
    tool = SimpleNamespace(name="bisheng_code_interpreter", executor=executor)
    workspace = object()
    bind_cloud_workspace([tool], workspace, require_e2b=False)
    assert executor.workspace_backend is workspace
