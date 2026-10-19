"""Exercise compaction boundaries and the actual main/child SDK assembly."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from deepagents.backends import FilesystemBackend
from deepagents.middleware.summarization import SummarizationMiddleware
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage, HumanMessage

from bisheng.linsight.domain.services.context_compaction import build_context_compaction


class CompactionTestModel(FakeMessagesListChatModel):
    model_name: str = "linsight-compaction-test"

    def bind_tools(self, tools, **kwargs):
        return self


@pytest.mark.parametrize("tokens, expected", [(170_000, False), (199_999, False), (200_000, True)])
def test_default_compaction_boundary(tmp_path, tokens, expected):
    model = CompactionTestModel(responses=[AIMessage(content="summary")])
    middleware = build_context_compaction(model, FilesystemBackend(root_dir=tmp_path, virtual_mode=True))
    assert middleware._should_summarize([HumanMessage(content="context")], tokens) is expected
    assert middleware._lc_helper.keep == ("messages", 6)
    assert model.profile is None


def test_known_smaller_model_keeps_its_capacity_based_trigger(tmp_path):
    model = CompactionTestModel(responses=[AIMessage(content="summary")], profile={"max_input_tokens": 32_000})
    middleware = build_context_compaction(model, FilesystemBackend(root_dir=tmp_path, virtual_mode=True))
    assert not middleware._should_summarize([], 27_199)
    assert middleware._should_summarize([], 27_200)
    assert model.profile["max_input_tokens"] == 32_000


@pytest.mark.parametrize("configured_member", [False, True])
async def test_real_factory_has_one_compactor_per_main_and_child(monkeypatch, tmp_path, configured_member):
    import deepagents.graph
    import deepagents.middleware.subagents

    from bisheng.linsight.domain.services import agent_factory, configured_subagents
    from bisheng.linsight.domain.services.subagent_context import DelegationMiddleware

    model = CompactionTestModel(responses=[AIMessage(content="ready")])
    monkeypatch.setattr(agent_factory, "_resolve_model", AsyncMock(return_value=model))
    monkeypatch.setattr(agent_factory, "settings", SimpleNamespace(get_linsight_conf=lambda: SimpleNamespace()))
    child_model = CompactionTestModel(model_name="explicit-child-test", responses=[AIMessage(content="child")])

    async def members(session, backend, directory, middleware, resources):
        return [
            {
                "name": "configured-child",
                "description": "Independent child",
                "system_prompt": "Return evidence",
                "model": child_model,
                "tools": [],
                "middleware": middleware([]),
            }
        ], DelegationMiddleware({})

    monkeypatch.setattr(configured_subagents, "build_configured_subagents", members)
    stacks = []
    create_agent = deepagents.graph.create_agent

    def capture_stack(*args, **kwargs):
        stacks.append(kwargs["middleware"])
        return create_agent(*args, **kwargs)

    monkeypatch.setattr(deepagents.graph, "create_agent", capture_stack)
    monkeypatch.setattr(deepagents.middleware.subagents, "create_agent", capture_stack)
    session = SimpleNamespace(
        id="compaction-test", agent_config={"subagents": []} if configured_member else None, tenant_id=1, user_id=1
    )
    graph = await agent_factory.create_linsight_agent(
        session, [], backend=FilesystemBackend(root_dir=tmp_path, virtual_mode=True)
    )
    assert len(stacks) >= 5
    compactors = []
    for stack in stacks:
        items = [item for item in stack if isinstance(item, SummarizationMiddleware)]
        assert len(items) == 1
        assert items[0]._lc_helper.trigger == ("tokens", 200_000)
        compactors.extend(items)
    assert len({id(item) for item in compactors}) == len(stacks)
    if configured_member:
        assert any(item._lc_helper.model is child_model for item in compactors)
    result = await graph.ainvoke(
        {"messages": [HumanMessage(content="hello")]}, {"configurable": {"thread_id": "compaction-test"}}
    )
    assert result["messages"][-1].content == "ready"
