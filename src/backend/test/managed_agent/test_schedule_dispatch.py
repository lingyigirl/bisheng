"""F078 T-22: app_type=100 dispatch, observation and runner failure accounting."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from bisheng.common.errcode.scheduled_task import ScheduledTaskConfigInvalidError
from bisheng.managed_agent.domain.constants import MANAGED_AGENT_APP_TYPE
from bisheng.managed_agent.domain.models.dao import ManagedAgentDao
from bisheng.scheduled_task.domain.services import run_service
from bisheng.worker.scheduled_task import managed_agent_runner


def _task(app_type, app_id="1", schedule_type="cron"):
    return SimpleNamespace(
        id=10,
        app_id=app_id,
        app_type=app_type,
        schedule_type=schedule_type,
        tenant_id=7,
        service_account_id=5,
        run_identity_user_id=42,
        preset_inputs=[{"key": "query", "value": "hi"}],
        last_trace_id="trace-1",
        run_lease_token="lease-1",
        last_run_at=None,
        run_started_at=None,
        consecutive_fail_count=0,
    )


def _agent(source_kind="high_code", native_app_type=None, native_app_id=None):
    return SimpleNamespace(
        id=1,
        source_kind=source_kind,
        native_app_type=native_app_type,
        native_app_id=native_app_id,
    )


async def test_dispatch_unknown_type_fails_closed():
    with pytest.raises(ScheduledTaskConfigInvalidError):
        await run_service._dispatch(_task(99), trace_id="t")


async def test_dispatch_external_managed_enqueues_runner(monkeypatch):
    async def _aget(agent_id):
        return _agent()

    monkeypatch.setattr(ManagedAgentDao, "aget", _aget)
    apply_async = MagicMock()
    monkeypatch.setattr(managed_agent_runner.execute_managed_agent_run, "apply_async", apply_async)

    await run_service._dispatch(_task(MANAGED_AGENT_APP_TYPE), trace_id="t", trigger_kind="cron")
    apply_async.assert_called_once()
    assert apply_async.call_args.kwargs["args"] == [10, "t", "cron"]


async def test_dispatch_native_workflow_uses_native_flow_id(monkeypatch):
    async def _aget(agent_id):
        return _agent(source_kind="native", native_app_type=10, native_app_id="flow-1")

    monkeypatch.setattr(ManagedAgentDao, "aget", _aget)
    called = AsyncMock()
    monkeypatch.setattr(run_service, "_dispatch_workflow", called)

    await run_service._dispatch(_task(MANAGED_AGENT_APP_TYPE), trace_id="t")
    called.assert_awaited_once()
    assert called.call_args.args[1] == "t"
    assert called.call_args.kwargs["flow_id"] == "flow-1"


@pytest.mark.parametrize("trigger_kind,expected", [("cron", True), ("manual", False), ("event", False)])
async def test_runner_failure_counting_follows_trigger_kind(monkeypatch, trigger_kind, expected):
    async def _get_task(task_id):
        return _task(MANAGED_AGENT_APP_TYPE)

    async def _get_agent(agent_id):
        return _agent()

    async def _fail(*args, **kwargs):
        from bisheng.common.errcode.managed_agent import ManagedAgentUpstreamError

        raise ManagedAgentUpstreamError(msg="boom")

    finalize = AsyncMock()
    monkeypatch.setattr(managed_agent_runner.ScheduledTaskDao, "aget", _get_task)
    monkeypatch.setattr(managed_agent_runner.ManagedAgentDao, "aget", _get_agent)
    monkeypatch.setattr(managed_agent_runner.invoke_service, "_execute", _fail)
    monkeypatch.setattr(managed_agent_runner.run_service, "_finalize", finalize)
    monkeypatch.setattr(managed_agent_runner.run_service, "_now", lambda: __import__("datetime").datetime.now())

    await managed_agent_runner._execute(10, "t", trigger_kind)
    assert finalize.call_args.kwargs["count_failure"] is expected


async def test_observe_runs_observes_native_workflow_projection(monkeypatch):
    async def _list(now):
        return [_task(MANAGED_AGENT_APP_TYPE)]

    async def _aget(agent_id):
        return _agent(source_kind="native", native_app_type=10, native_app_id="flow-1")

    read_state = AsyncMock(return_value={"status": "SUCCESS"})
    finalize = AsyncMock()
    monkeypatch.setattr(run_service.ScheduledTaskDao, "alist_with_live_lease", _list)
    monkeypatch.setattr(ManagedAgentDao, "aget", _aget)
    monkeypatch.setattr(run_service, "_finalize", finalize)

    observed = await run_service.observe_runs(read_state=read_state)
    assert observed == 1
    finalize.assert_awaited_once()
    read_state.assert_awaited_once_with("trace-1")


async def test_observe_runs_skips_external_managed_runs(monkeypatch):
    async def _list(now):
        return [_task(MANAGED_AGENT_APP_TYPE)]

    async def _aget(agent_id):
        return _agent()

    read_state = AsyncMock()
    monkeypatch.setattr(run_service.ScheduledTaskDao, "alist_with_live_lease", _list)
    monkeypatch.setattr(ManagedAgentDao, "aget", _aget)

    observed = await run_service.observe_runs(read_state=read_state)
    assert observed == 0
    read_state.assert_not_called()
