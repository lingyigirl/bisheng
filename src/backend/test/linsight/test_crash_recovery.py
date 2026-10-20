"""Failure-oriented recovery tests using real Redis commands and tool middleware."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import fakeredis.aioredis
import pytest
from langchain_core.messages import ToolMessage

from bisheng.linsight.domain.services import recovery_middleware
from bisheng.linsight.domain.services.checkpointer import PlainRedisCheckpointer
from bisheng.linsight.domain.services.event_replay import EventReplayService
from bisheng.linsight.domain.services.recovery_context import (
    ExecutionFence,
    RecoveryBlocked,
    RecoveryLeaseLost,
    execution_fence,
    fenced_pipeline,
)
from bisheng.linsight.domain.services.recovery_middleware import RecoveryMiddleware, validate_receipts
from bisheng.linsight.domain.services.recovery_service import _RELEASE, _RENEW


@pytest.fixture
async def fence():
    server = fakeredis.FakeServer()
    redis = fakeredis.aioredis.FakeRedis(server=server)
    value = ExecutionFence("test-recovery", 1, "first", 1, redis, fakeredis.FakeRedis(server=server))
    await redis.set(value.lease_key, value.token, ex=30)
    await redis.set(value.prefix + ":receipt-count", "0", ex=604800)
    token = execution_fence.set(value)
    try:
        yield value
    finally:
        execution_fence.reset(token)
        await redis.aclose()


def request(name="read_file", call_id="call-1", args=None, external=False):
    def builtin():
        return None

    builtin.__module__ = "deepagents.middleware.filesystem"
    return SimpleNamespace(
        tool_call={"id": call_id, "name": name, "args": args or {"path": "/input.txt"}},
        tool=SimpleNamespace(tool_id=6) if external else SimpleNamespace(func=builtin),
    )


@pytest.fixture(autouse=True)
def tool_namespace(monkeypatch):
    monkeypatch.setattr(recovery_middleware, "get_config", lambda: {"configurable": {"checkpoint_ns": "child:1"}})


async def test_stale_owner_cannot_renew_release_or_append(fence):
    await fence.redis.set(fence.lease_key, "replacement", ex=30)
    assert await fence.redis.eval(_RENEW, 1, fence.lease_key, fence.token, 30) == 0
    assert await fence.redis.eval(_RELEASE, 1, fence.lease_key, fence.token) == 0
    with pytest.raises(RecoveryLeaseLost):
        await EventReplayService(fence.redis, 1, fence.task_id).append({"event_type": "final_result", "data": {}})
    assert await fence.redis.get(fence.lease_key) == b"replacement"


async def test_successful_tool_result_is_reused_without_reexecution(fence):
    handler = AsyncMock(return_value=ToolMessage(content="original evidence", tool_call_id="call-1"))
    middleware = RecoveryMiddleware()
    first = await middleware.awrap_tool_call(request(), handler)
    second = await middleware.awrap_tool_call(request(), handler)
    assert second.content == first.content
    handler.assert_awaited_once()


async def test_unknown_external_outcome_blocks_retry_and_recovery(fence):
    handler = AsyncMock(side_effect=RuntimeError("connection lost after server accepted request"))
    middleware = RecoveryMiddleware()
    with pytest.raises(RuntimeError):
        await middleware.awrap_tool_call(request("send_email", external=True), handler)
    with pytest.raises(RecoveryBlocked):
        await validate_receipts(fence)
    with pytest.raises(RecoveryBlocked):
        await middleware.awrap_tool_call(request("send_email", external=True), handler)
    handler.assert_awaited_once()


async def test_read_retry_allowed_but_external_tool_cannot_spoof_read_name(fence):
    middleware = RecoveryMiddleware()
    failure = AsyncMock(side_effect=RuntimeError("read interrupted"))
    with pytest.raises(RuntimeError):
        await middleware.awrap_tool_call(request(), failure)
    await validate_receipts(fence)
    assert await middleware.awrap_tool_call(request(), AsyncMock(return_value="ok")) == "ok"
    with pytest.raises(RuntimeError):
        await middleware.awrap_tool_call(request(call_id="external", external=True), failure)
    with pytest.raises(RecoveryBlocked):
        await validate_receipts(fence)


async def test_receipt_rejects_parameter_mismatch(fence):
    middleware = RecoveryMiddleware()
    await middleware.awrap_tool_call(request(), AsyncMock(return_value="ok"))
    with pytest.raises(RecoveryBlocked):
        await middleware.awrap_tool_call(request(args={"path": "/other.txt"}), AsyncMock())


async def test_lost_lease_does_not_publish_late_tool_result(fence):
    async def handler(req):
        await fence.redis.set(fence.lease_key, "replacement")
        return "external response"

    with pytest.raises(RecoveryLeaseLost):
        await RecoveryMiddleware().awrap_tool_call(request("write_file"), handler)
    with pytest.raises(RecoveryBlocked):
        await validate_receipts(fence)


async def test_stateful_code_blocks_recovery_even_after_result(fence):
    await RecoveryMiddleware().awrap_tool_call(
        request("bisheng_code_interpreter", external=True), AsyncMock(return_value="ok")
    )
    with pytest.raises(RecoveryBlocked, match="Sandbox"):
        await validate_receipts(fence)


async def test_completed_connector_result_is_reused_without_resending(fence):
    handler = AsyncMock(return_value=ToolMessage(content="remote receipt 42", tool_call_id="call-1"))
    middleware = RecoveryMiddleware()
    first = await middleware.awrap_tool_call(request("external_action", external=True), handler)
    await validate_receipts(fence)
    second = await middleware.awrap_tool_call(request("external_action", external=True), handler)
    assert second.content == first.content
    handler.assert_awaited_once()


@pytest.mark.parametrize("name", ["write_file", "edit_file", "bisheng_code_interpreter", "job_output"])
async def test_unknown_write_sandbox_or_ephemeral_job_is_never_replayed(fence, name):
    handler = AsyncMock(side_effect=RuntimeError("worker interrupted"))
    middleware = RecoveryMiddleware()
    call = request(name, external=name in {"bisheng_code_interpreter", "job_output"})
    with pytest.raises(RuntimeError):
        await middleware.awrap_tool_call(call, handler)
    with pytest.raises(RecoveryBlocked):
        await validate_receipts(fence)
    with pytest.raises(RecoveryBlocked):
        await middleware.awrap_tool_call(call, handler)
    handler.assert_awaited_once()


async def test_missing_receipt_hash_is_not_treated_as_no_previous_tools(fence):
    await RecoveryMiddleware().awrap_tool_call(request(), AsyncMock(return_value="durable"))
    await fence.redis.delete(fence.prefix + ":receipts")
    with pytest.raises(RecoveryBlocked, match="metadata"):
        await validate_receipts(fence)


async def test_checkpoint_rejects_obsolete_writer(fence, monkeypatch):
    client = SimpleNamespace(async_connection=fence.redis, async_pipeline=lambda **kw: fence.redis.pipeline(**kw))
    saver = PlainRedisCheckpointer()
    monkeypatch.setattr(saver, "_get_redis_client", AsyncMock(return_value=client))
    config = {"configurable": {"thread_id": fence.task_id, "checkpoint_id": "one"}}
    await saver.aput_writes(config, [("messages", "original")], "node")
    await fence.redis.set(fence.lease_key, "replacement")
    with pytest.raises(RecoveryLeaseLost):
        await saver.aput_writes(config, [("messages", "stale")], "node")
    rows = await saver._fetch_pending_writes(fence.redis, fence.task_id, "", "one")
    assert rows[0][2] == "original"


async def test_healthy_lease_renewal_does_not_abort_transaction(fence):
    client = SimpleNamespace(async_pipeline=lambda **kw: fence.redis.pipeline(**kw))
    async with fenced_pipeline(client) as pipe:
        await pipe.set("durable-result", "one")
        await fence.redis.eval(_RENEW, 1, fence.lease_key, fence.token, 30)
        await pipe.execute()
    assert await fence.redis.get("durable-result") == b"one"


async def test_takeover_during_transaction_aborts_all_writes(fence):
    client = SimpleNamespace(async_pipeline=lambda **kw: fence.redis.pipeline(**kw))
    with pytest.raises(RecoveryLeaseLost):
        async with fenced_pipeline(client) as pipe:
            await pipe.set("durable-result", "stale")
            await fence.redis.set(fence.lease_key, "new-owner")
            await pipe.execute()
    assert await fence.redis.get("durable-result") is None


async def test_real_graph_continues_checkpoint_without_repeating_completed_node(fence, monkeypatch):
    from typing import TypedDict

    from langgraph.graph import END, START, StateGraph

    class State(TypedDict):
        result: str

    client = SimpleNamespace(async_connection=fence.redis, async_pipeline=lambda **kw: fence.redis.pipeline(**kw))
    saver = PlainRedisCheckpointer()
    monkeypatch.setattr(saver, "_get_redis_client", AsyncMock(return_value=client))
    entered = asyncio.Event()
    first_calls = []

    async def first(state):
        first_calls.append(1)
        return {"result": "persisted"}

    async def interrupted(state):
        entered.set()
        await asyncio.Event().wait()

    def assemble(last):
        graph = StateGraph(State)
        graph.add_node("first", first)
        graph.add_node("last", last)
        graph.add_edge(START, "first")
        graph.add_edge("first", "last")
        graph.add_edge("last", END)
        return graph.compile(checkpointer=saver)

    config = {"configurable": {"thread_id": fence.task_id}}
    run = asyncio.create_task(assemble(interrupted).ainvoke({"result": "new"}, config))
    await asyncio.wait_for(entered.wait(), timeout=5)
    run.cancel()
    with pytest.raises(asyncio.CancelledError):
        await run

    async def resumed(state):
        return {"result": state["result"] + " recovered"}

    result = await assemble(resumed).ainvoke(None, config)
    assert result == {"result": "persisted recovered"}
    assert len(first_calls) == 1


@pytest.mark.parametrize("epoch,status", [(2, "IN_PROGRESS"), (1, "TERMINATED")])
async def test_database_fence_rejects_superseded_epoch_or_user_stop(fence, epoch, status):
    import sqlalchemy as sa
    from sqlalchemy.orm import Session

    from bisheng.linsight.domain.repositories.recovery_repository import RecoveryRepository

    engine = sa.create_engine("sqlite://")
    table = sa.Table(
        "linsight_session_version",
        sa.MetaData(),
        sa.Column("id", sa.String, primary_key=True),
        sa.Column("recovery_epoch", sa.Integer),
        sa.Column("status", sa.String),
    )
    table.create(engine)
    with engine.begin() as connection:
        connection.execute(table.insert().values(id=fence.task_id, recovery_epoch=epoch, status=status))
    with Session(engine) as session, pytest.raises(RecoveryLeaseLost):
        RecoveryRepository.guard(session)
    with engine.connect() as connection:
        assert connection.execute(sa.select(table.c.status)).scalar_one() == status
    engine.dispose()


async def test_concurrent_workers_only_dispatch_one_executor(fence, monkeypatch):
    from bisheng.linsight.domain.services import recovery_service

    await fence.redis.delete(fence.lease_key)
    task = SimpleNamespace(id=fence.task_id, tenant_id=1, recovery_epoch=1, recovery_state={"attempts": 0})
    repository = SimpleNamespace(claim=AsyncMock(return_value=task))
    monkeypatch.setattr(recovery_service, "RecoveryRepository", lambda: repository)
    monkeypatch.setattr(
        recovery_service,
        "get_redis_client",
        AsyncMock(return_value=SimpleNamespace(async_connection=fence.redis, connection=fence.sync_redis)),
    )
    entered, release = asyncio.Event(), asyncio.Event()

    async def run(task_id):
        entered.set()
        await release.wait()

    executor = SimpleNamespace(async_run=AsyncMock(side_effect=run))
    manager = SimpleNamespace(register_task_ownership=AsyncMock())
    first = asyncio.create_task(recovery_service.RecoveryService().run(task, {}, executor, manager))
    await asyncio.wait_for(entered.wait(), 5)
    await recovery_service.RecoveryService().run(task, {}, executor, manager)
    release.set()
    await first
    executor.async_run.assert_awaited_once()
    repository.claim.assert_awaited_once()


@pytest.mark.parametrize("recovering", [False, True])
async def test_durable_followup_dispatch_survives_missing_queue_question(fence, monkeypatch, recovering):
    from bisheng.linsight.domain.services import recovery_service

    await fence.redis.delete(fence.lease_key)
    await fence.redis.set(fence.prefix + ":recovery-marker", "1")
    task = SimpleNamespace(
        id=fence.task_id,
        tenant_id=1,
        recovery_epoch=2,
        recovery_state={
            "attempts": 1,
            "continuation": {"id": "new-turn", "question": "DURABLE QUESTION", "receipt_epoch": 2},
        },
    )
    monkeypatch.setattr(
        recovery_service, "RecoveryRepository", lambda: SimpleNamespace(claim=AsyncMock(return_value=task))
    )
    monkeypatch.setattr(
        recovery_service,
        "get_redis_client",
        AsyncMock(
            return_value=SimpleNamespace(
                async_connection=fence.redis,
                connection=fence.sync_redis,
            )
        ),
    )
    executor = SimpleNamespace(async_continue=AsyncMock(), async_recover=AsyncMock())
    manager = SimpleNamespace(register_task_ownership=AsyncMock())
    await recovery_service.RecoveryService().run(task, {"recover": recovering}, executor, manager)
    executor.async_continue.assert_awaited_once_with(task.id, question="DURABLE QUESTION")
    executor.async_recover.assert_not_awaited()
    if recovering:
        assert executor._crash_recovery is True


@pytest.mark.parametrize("parked", [False, True])
async def test_continued_turn_takeover_emits_recovery_or_restores_interrupt(fence, parked):
    from bisheng.linsight.domain.task_exec import LinsightWorkflowTask

    task = LinsightWorkflowTask.__new__(LinsightWorkflowTask)
    task._state_manager = SimpleNamespace(get_execution_tasks=AsyncMock(return_value=[]))
    task._handle_need_user_input = AsyncMock()
    pending = [SimpleNamespace(interrupts=[SimpleNamespace(value={"reason": "Choose a year"})])] if parked else []
    saved = SimpleNamespace(values={}, tasks=pending)
    agent = SimpleNamespace(aget_state=AsyncMock(return_value=saved))
    session = SimpleNamespace(id=fence.task_id, tenant_id=1, recovery_state={"attempts": 1})

    result = await task._prepare_recovered_graph(agent, session, allow_empty=True)

    if parked:
        assert result is None
        task._handle_need_user_input.assert_awaited_once()
    else:
        assert result is saved
        assert await fence.redis.xlen(fence.prefix + ":events") == 1
        task._handle_need_user_input.assert_not_awaited()


async def test_explicit_new_turn_skips_old_sandbox_state_but_never_current_state(fence):
    middleware = RecoveryMiddleware()
    handler = AsyncMock(return_value="executed")
    await middleware.awrap_tool_call(request("bisheng_code_interpreter"), handler)
    # An explicit new user turn does not replay code from the previous turn.
    await validate_receipts(fence, minimum_epoch=2)
    with pytest.raises(RecoveryBlocked):
        await validate_receipts(fence, minimum_epoch=1)


async def test_workspace_uses_last_completed_copy_after_interrupted_restore(fence, monkeypatch, tmp_path):
    from bisheng.linsight.domain.services import recovery_workspace
    from bisheng.linsight.domain.services.workspace_backend import WorkspaceBackend

    class Storage:
        bucket = "test"

        def __init__(self):
            self.objects = {}

        def put_object_sync(self, *, object_name, file, **kwargs):
            self.objects[object_name] = file

        def get_object_sync(self, *, object_name, **kwargs):
            return self.objects.get(object_name)

        def list_objects_sync(self, bucket, prefix):
            return [
                SimpleNamespace(object_name=key, size=len(value), is_dir=False)
                for key, value in list(self.objects.items())
                if key.startswith(prefix)
            ]

    storage = Storage()
    source = WorkspaceBackend(fence.task_id + "/attempt-1", storage, str(tmp_path / "old"))
    source.write("/scratch/nested/proof.txt", "persisted evidence")
    # Epoch two crashed during copying; epoch three must copy epoch one again.
    fence.epoch = 3
    session = SimpleNamespace(id=fence.task_id, recovery_state={"workspace_epoch": 1})
    ready = AsyncMock(return_value={"workspace_epoch": 3})
    monkeypatch.setattr(recovery_workspace, "RecoveryRepository", lambda: SimpleNamespace(workspace_ready=ready))
    target = await recovery_workspace.restore_attempt_workspace(session, storage, str(tmp_path / "new"), fence)
    assert target.download_files(["/scratch/nested/proof.txt"])[0].content == b"persisted evidence"
    assert storage.objects[f"workspace/{fence.task_id}/attempt-1/scratch/nested/proof.txt"] == b"persisted evidence"
    ready.assert_awaited_once_with(fence.task_id, 3)


async def test_recovery_projection_keeps_completed_tools_and_answered_questions():
    from bisheng.linsight.domain.services.recovery_projection import reset_incomplete_projection

    tool = {"call_id": "read", "step_type": "tool", "status": "end", "output": "evidence"}
    question = {"step_type": "call_user_input", "is_completed": True, "user_input": "A"}
    task = SimpleNamespace(
        id="task",
        status="in_progress",
        history=[tool, question, {"call_id": "model", "step_type": "thinking", "status": "end", "output": "partial"}],
    )
    manager = SimpleNamespace(
        get_execution_tasks=AsyncMock(return_value=[task]), update_execution_task_status=AsyncMock()
    )
    await reset_incomplete_projection(manager)
    manager.update_execution_task_status.assert_awaited_once_with(
        "task", status="in_progress", history=[tool, question]
    )


async def test_completion_commit_without_final_frame_requests_snapshot():
    from bisheng.linsight.domain.services.replay_stream import stream_replay

    websocket = SimpleNamespace(accept=AsyncMock(), send_json=AsyncMock(), close=AsyncMock())
    replay = SimpleNamespace(valid_cursor=AsyncMock(return_value=True), read_after=AsyncMock(return_value=[]))
    await stream_replay(websocket, replay, "1-0", is_terminal=AsyncMock(return_value=True))
    websocket.send_json.assert_awaited_once_with({"event_type": "replay_reset", "data": {"reason": "terminal_state"}})
    websocket.close.assert_awaited_once_with(code=1000)


def test_resumed_delegation_closes_without_replaying_ai_tool_call_start():
    from bisheng.linsight.domain.services.stream_event_mapper import StreamEventMapper

    mapper = StreamEventMapper(svid="task")
    mapper.normalize(
        "custom",
        {
            "type": "subagent_identity",
            "agent_id": "member",
            "agent_name": "Member",
            "invocation_id": "call-1",
            "parent_call_id": "call-1",
            "delegate_goal": "Calculate",
        },
    )
    result = mapper.normalize("messages", (ToolMessage(content="recovered result", tool_call_id="call-1"), {}))
    assert len(result) == 1
    assert result[0].status == "end"
    assert result[0].extra_info["invocation_id"] == "call-1"
    assert result[0].output == "recovered result"


@pytest.mark.parametrize("attempts,marker", [(4, True), (1, False)])
async def test_recovery_budget_or_missing_marker_blocks_before_graph(fence, monkeypatch, attempts, marker):
    from bisheng.linsight.domain.services import recovery_service, state_message_manager

    await fence.redis.delete(fence.lease_key)
    if marker:
        await fence.redis.set(fence.prefix + ":recovery-marker", "1")
    task = SimpleNamespace(id=fence.task_id, tenant_id=1, recovery_epoch=2, recovery_state={"attempts": attempts})
    monkeypatch.setattr(
        recovery_service, "RecoveryRepository", lambda: SimpleNamespace(claim=AsyncMock(return_value=task))
    )
    monkeypatch.setattr(
        recovery_service,
        "get_redis_client",
        AsyncMock(return_value=SimpleNamespace(async_connection=fence.redis, connection=fence.sync_redis)),
    )
    monkeypatch.setattr(state_message_manager, "LinsightStateMessageManager", lambda _: SimpleNamespace())
    executor = SimpleNamespace(async_recover=AsyncMock(), _handle_execution_error=AsyncMock())
    await recovery_service.RecoveryService().run(
        task, {"recover": True}, executor, SimpleNamespace(register_task_ownership=AsyncMock())
    )
    executor.async_recover.assert_not_awaited()
    error = executor._handle_execution_error.await_args.args[0]
    assert isinstance(error, RecoveryBlocked)


async def test_lease_is_renewed_while_database_claim_waits(fence, monkeypatch):
    from bisheng.linsight.domain.services import recovery_service

    await fence.redis.delete(fence.lease_key)
    entered, release = asyncio.Event(), asyncio.Event()

    async def claim(*args, **kwargs):
        entered.set()
        await release.wait()
        return None

    monkeypatch.setattr(recovery_service, "RecoveryRepository", lambda: SimpleNamespace(claim=claim))
    monkeypatch.setattr(
        recovery_service,
        "get_redis_client",
        AsyncMock(return_value=SimpleNamespace(async_connection=fence.redis, connection=fence.sync_redis)),
    )
    service = recovery_service.RecoveryService()
    service.LEASE_TTL = 1
    task = SimpleNamespace(id=fence.task_id, tenant_id=1)
    running = asyncio.create_task(service.run(task, {}, SimpleNamespace(), SimpleNamespace()))
    try:
        await entered.wait()
        await asyncio.sleep(1.2)
        assert await fence.redis.exists(fence.lease_key)
    finally:
        release.set()
        await running
