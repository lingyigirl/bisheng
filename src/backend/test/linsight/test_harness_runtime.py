"""Real graph execution with deterministic models; no provider or sandbox calls."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import fakeredis.aioredis
import pytest
from deepagents import create_deep_agent
from langchain.agents.middleware import AgentMiddleware
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from bisheng.linsight.domain.services.checkpointer import PlainRedisCheckpointer
from bisheng.linsight.domain.services.subagent_context import DelegationMiddleware, SubagentIdentityMiddleware


class ToolModel(FakeMessagesListChatModel):
    def bind_tools(self, tools, **kwargs):
        return self


@pytest.mark.parametrize("interrupt_before_merge", [False, True])
async def test_plain_redis_rebuild_preserves_both_completed_child_answers(monkeypatch, interrupt_before_merge):
    """Exercise the actual deepagents message channel, not a scalar state graph."""
    redis = fakeredis.aioredis.FakeRedis()
    saver = PlainRedisCheckpointer()
    monkeypatch.setattr(
        saver,
        "_get_redis_client",
        AsyncMock(
            return_value=SimpleNamespace(async_connection=redis, async_pipeline=lambda **kw: redis.pipeline(**kw))
        ),
    )
    entered = asyncio.Event()

    class BeforeMerge(AgentMiddleware):
        async def awrap_model_call(self, request, handler):
            if any(isinstance(message, ToolMessage) and message.name == "task" for message in request.messages):
                entered.set()
                await asyncio.Event().wait()
            return await handler(request)

    class CountingChild(ToolModel):
        calls: int = 0

        async def _agenerate(self, *args, **kwargs):
            self.calls += 1
            return await super()._agenerate(*args, **kwargs)

    def assemble(responses, child, middleware):
        return create_deep_agent(
            model=ToolModel(responses=responses),
            checkpointer=saver,
            middleware=middleware,
            subagents=[
                {
                    "name": "general-purpose",
                    "description": "Research",
                    "system_prompt": "Research",
                    "model": child,
                    "tools": [],
                }
            ],
        )

    child = CountingChild(responses=[AIMessage(content="CHILD-DURABLE")])
    calls = [
        {
            "id": f"child-{i}",
            "name": "task",
            "args": {"description": f"research-{i}", "subagent_type": "general-purpose"},
        }
        for i in range(2)
    ]
    config = {"configurable": {"thread_id": "parallel-checkpoint"}}
    first = assemble(
        [AIMessage(content="", tool_calls=calls), AIMessage(content="COMBINED-DURABLE")],
        child,
        [BeforeMerge()] if interrupt_before_merge else [],
    )
    run = asyncio.create_task(first.ainvoke({"messages": [HumanMessage(content="Research")]}, config))
    try:
        if interrupt_before_merge:
            await asyncio.wait_for(entered.wait(), 10)
            run.cancel()
            with pytest.raises(asyncio.CancelledError):
                await run
        else:
            await run
        assert child.calls == 2
        unused_child = CountingChild(responses=[AIMessage(content="MUST-NOT-RERUN")])
        rebuilt = assemble([AIMessage(content="COMBINED-DURABLE")], unused_child, [])
        saved = await rebuilt.aget_state(config, subgraphs=True)
        assert bool(saved.next) is interrupt_before_merge
        if interrupt_before_merge:
            result = await rebuilt.ainvoke(None, config)
        else:
            result = saved.values
        assert result["messages"][-1].content == "COMBINED-DURABLE"
        answers = [
            message for message in result["messages"] if isinstance(message, ToolMessage) and message.name == "task"
        ]
        assert len(answers) == 2
        assert all(message.content == "CHILD-DURABLE" for message in answers)
        assert unused_child.calls == 0
    finally:
        if not run.done():
            run.cancel()
            await asyncio.gather(run, return_exceptions=True)
        await redis.aclose()


async def test_parallel_delegations_keep_actual_invocation_identity():
    calls = [
        {"id": f"call-{i}", "name": "task", "args": {"subagent_type": "general-purpose", "description": f"Goal {i}"}}
        for i in range(2)
    ]
    root = ToolModel(responses=[AIMessage(content="", tool_calls=calls), AIMessage(content="Combined")])
    child = ToolModel(responses=[AIMessage(content="Evidence")])
    graph = create_deep_agent(
        model=root,
        middleware=[DelegationMiddleware({"general-purpose": {"id": "research", "name": "Research"}})],
        subagents=[
            {
                "name": "general-purpose",
                "description": "Research",
                "system_prompt": "Research",
                "model": child,
                "tools": [],
                "middleware": [SubagentIdentityMiddleware()],
            }
        ],
    )
    identities = []
    async for ns, mode, chunk in graph.astream(
        {"messages": [HumanMessage(content="Research two topics")]},
        stream_mode=["custom", "updates"],
        subgraphs=True,
    ):
        if mode == "custom" and chunk.get("type") == "subagent_identity":
            identities.append((ns, chunk))
    children = [(ns, value) for ns, value in identities if ns]
    assert len(children) >= 2
    assert {value["invocation_id"] for _, value in children} == {"call-0", "call-1"}
    assert len({ns for ns, _ in children}) == 2
    assert {value["agent_id"] for _, value in children} == {"research"}


async def test_rebuilt_graph_resumes_human_interrupt_before_delegation():
    """AC-14: reconstruct the graph around the same checkpoint, then delegate."""
    from langchain_core.tools import tool
    from langgraph.checkpoint.memory import InMemorySaver
    from langgraph.types import Command, interrupt

    @tool
    def ask_user(question: str) -> str:
        """Ask for missing task information."""
        return interrupt(question)

    checkpoint = InMemorySaver()
    config = {"configurable": {"thread_id": "resume-task"}}

    def assemble(responses):
        return create_deep_agent(
            model=ToolModel(responses=responses),
            tools=[ask_user],
            checkpointer=checkpoint,
            middleware=[DelegationMiddleware({"general-purpose": {"id": "research", "name": "Research"}})],
            subagents=[
                {
                    "name": "general-purpose",
                    "description": "Research",
                    "system_prompt": "Research",
                    "model": ToolModel(responses=[AIMessage(content="Evidence")]),
                    "tools": [],
                    "middleware": [SubagentIdentityMiddleware()],
                }
            ],
        )

    first = assemble(
        [AIMessage(content="", tool_calls=[{"id": "ask", "name": "ask_user", "args": {"question": "Which year?"}}])]
    )
    parked = await first.ainvoke({"messages": [HumanMessage(content="Research")]}, config)
    assert parked["__interrupt__"]
    resumed = assemble(
        [
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "id": "resumed-call",
                        "name": "task",
                        "args": {"description": "Research 2026", "subagent_type": "general-purpose"},
                    }
                ],
            ),
            AIMessage(content="Combined after clarification"),
        ]
    )
    result = await resumed.ainvoke(Command(resume="2026"), config)
    assert result["messages"][-1].content == "Combined after clarification"
    assert any(getattr(message, "tool_call_id", None) == "resumed-call" for message in result["messages"])
