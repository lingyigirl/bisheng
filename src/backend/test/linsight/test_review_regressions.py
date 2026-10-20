"""Acceptance-review regressions: child fencing, durable follow-ups and artifacts."""

from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, MessagesState, StateGraph

from bisheng.linsight.domain.models.linsight_session_version import SessionVersionStatusEnum as Status
from bisheng.linsight.domain.services.recovery_middleware import RecoveryMiddleware
from bisheng.linsight.domain.task_exec import LinsightWorkflowTask


async def test_factory_guards_researcher_and_every_other_child(monkeypatch):
    import deepagents

    from bisheng.linsight.domain.services import agent_factory, configured_subagents, context_compaction

    monkeypatch.setattr(agent_factory, "_resolve_model", AsyncMock(return_value=object()))
    monkeypatch.setattr(agent_factory, "settings", SimpleNamespace(get_linsight_conf=lambda: SimpleNamespace()))
    monkeypatch.setattr(deepagents, "create_deep_agent", lambda **kwargs: kwargs)
    monkeypatch.setattr(context_compaction, "build_context_compaction", Mock(return_value=object()))

    async def members(session, backend, directory, middleware, resources):
        from bisheng.linsight.domain.services.subagent_context import DelegationMiddleware

        return [{"name": "member-test", "middleware": middleware([])}], DelegationMiddleware({})

    monkeypatch.setattr(configured_subagents, "build_configured_subagents", members)
    session = SimpleNamespace(id="test", agent_config={"recovery_protocol": 1}, tenant_id=1, user_id=1)
    graph = await agent_factory.create_linsight_agent(session, [])
    assert any(isinstance(item, RecoveryMiddleware) for item in graph["middleware"])
    assert {child["name"] for child in graph["subagents"]} >= {"general-purpose", "member-test", "data-analyst"}
    for child in graph["subagents"]:
        assert sum(isinstance(item, RecoveryMiddleware) for item in child["middleware"]) == 1, child["name"]


def repository_with_row(monkeypatch, row):
    from bisheng.linsight.domain.repositories import recovery_repository

    session = SimpleNamespace(
        execute=AsyncMock(return_value=SimpleNamespace(scalar_one=lambda: row, scalar_one_or_none=lambda: row)),
        commit=AsyncMock(),
        refresh=AsyncMock(),
    )

    @asynccontextmanager
    async def database():
        yield session

    monkeypatch.setattr(recovery_repository, "get_async_db_session", database)
    return recovery_repository.RecoveryRepository(), session


@pytest.mark.parametrize("recovering", [False, True])
async def test_followup_input_is_committed_before_claim_and_rejects_duplicate_submit(monkeypatch, recovering):
    row = SimpleNamespace(status=Status.COMPLETED, recovery_epoch=2, recovery_state={"workspace_epoch": 2})
    repository, session = repository_with_row(monkeypatch, row)
    assert await repository.prepare_continue("task", "NEW QUESTION")
    continuation = row.recovery_state["continuation"]
    assert continuation["question"] == "NEW QUESTION"
    assert continuation["receipt_epoch"] == 3
    assert row.recovery_state["workspace_epoch"] == 2
    session.commit.assert_awaited_once()
    assert not await repository.prepare_continue("task", "REPLACEMENT")
    claimed = await repository.claim("task", recovering=recovering)
    assert claimed.recovery_epoch == 3
    assert claimed.recovery_state["continuation"] == continuation
    assert await repository.claim("task") is None


async def test_persisted_output_baseline_preserves_other_recovery_state(monkeypatch):
    row = SimpleNamespace(recovery_state={"workspace_epoch": 2, "phase": "running"})
    repository, session = repository_with_row(monkeypatch, row)
    state = await repository.save_output_baseline("task", {"output/old.csv": "digest"})
    assert state == {
        "workspace_epoch": 2,
        "phase": "running",
        "inherited_output_md5": {"output/old.csv": "digest"},
    }
    session.commit.assert_awaited_once()


@pytest.mark.parametrize("checkpoint", ["missing", "pending", "complete"])
async def test_continuation_checkpoint_accepts_input_exactly_once(monkeypatch, checkpoint):
    from bisheng.linsight.domain import task_exec

    monkeypatch.setattr(task_exec, "settings", SimpleNamespace(get_linsight_conf=lambda: SimpleNamespace()))
    crash = checkpoint == "pending"
    calls = []

    async def answer(state):
        nonlocal crash
        if crash:
            crash = False
            raise RuntimeError("worker died after input checkpoint")
        calls.append(1)
        return {"messages": [AIMessage(content="CONTINUE-FIX-OK")]}

    graph = StateGraph(MessagesState)
    graph.add_node("answer", answer)
    graph.add_edge(START, "answer")
    graph.add_edge("answer", END)
    agent = graph.compile(checkpointer=InMemorySaver())
    config = {"configurable": {"thread_id": "task"}}
    if checkpoint != "missing":
        try:
            await agent.ainvoke({"messages": [HumanMessage(content="new question", id="continue-request")]}, config)
        except RuntimeError:
            assert checkpoint == "pending"
    task = LinsightWorkflowTask.__new__(LinsightWorkflowTask)
    task._current_time_block = lambda: "now"
    task._capture_values_snapshot = Mock()
    task._handle_event = AsyncMock()
    task._state_manager = SimpleNamespace(flush_step_projections=AsyncMock())
    session = SimpleNamespace(id="task", recovery_state={"continuation": {"id": "request"}})

    await task._drive_continue(agent, session, "new question")

    saved = await agent.aget_state(config)
    assert sum(message.id == "continue-request" for message in saved.values["messages"]) == 1
    assert saved.values["messages"][-1].content == "CONTINUE-FIX-OK"
    assert len(calls) == 1


async def test_inherited_artifact_baseline_survives_new_attempt_directory(monkeypatch, tmp_path):
    import hashlib

    from bisheng.linsight.domain import task_exec
    from bisheng.linsight.domain.services import recovery_context, recovery_workspace, skill_provisioning

    inherited = tmp_path / "output" / "old.csv"
    inherited.parent.mkdir()
    inherited.write_bytes(b"previous turn")
    new = inherited.with_name("new.csv")
    new.write_bytes(b"new turn")
    session = SimpleNamespace(
        id="task",
        tenant_id=1,
        recovery_state={
            "inherited_output_md5": {"output/old.csv": hashlib.md5(b"previous turn").hexdigest()},
        },
    )
    task = LinsightWorkflowTask.__new__(LinsightWorkflowTask)
    task.file_dir = str(tmp_path)
    task._turn_budget = {}
    monkeypatch.setattr(task_exec, "get_minio_storage", AsyncMock(return_value=object()))
    monkeypatch.setattr(task_exec, "create_linsight_agent", AsyncMock())
    monkeypatch.setattr(recovery_workspace, "restore_attempt_workspace", AsyncMock(return_value=object()))
    monkeypatch.setattr(skill_provisioning, "materialize_session_skills", AsyncMock(return_value=[]))
    token = recovery_context.execution_fence.set(SimpleNamespace(epoch=2))
    try:
        await task._create_agent(session, [])
    finally:
        recovery_context.execution_fence.reset(token)

    assert [item["file_name"] for item in await task._read_deliverable_file_details()] == ["new.csv"]
    inherited.write_bytes(b"changed in this turn")
    assert sorted(item["file_name"] for item in await task._read_deliverable_file_details()) == ["new.csv", "old.csv"]
