"""F077 助手运行器的遥测归因（AC-52）。

不需要真实 Celery / broker：直接驱动 `_execute_assistant_run_async`，把 DAO、
Agent、终态回写与遥测全部替换为桩。
"""

from types import SimpleNamespace

import pytest

from bisheng.scheduled_task.domain.services.trigger_guard import AppState, IdentityState, RecheckOutcome
from bisheng.worker.scheduled_task import assistant_runner


def _task() -> SimpleNamespace:
    return SimpleNamespace(
        id=1,
        app_id="1001",
        app_type=5,
        name="nightly-task",
        run_identity_user_id=501,
        preset_inputs=[{"key": "question", "value": "hello"}],
        last_trace_id="stale-trace",
    )


class _FakeAssistant:
    name = "customer-assistant"


class _FakeAgent:
    def __init__(self, **kwargs):
        self.kwargs = kwargs

    async def init_assistant(self):
        return None

    async def run(self, question):
        return None


@pytest.fixture()
def patched_runner(monkeypatch):
    task = _task()
    captured = {}

    async def _aget(task_id):
        return task

    async def _recheck(_task):
        return RecheckOutcome(app_state=AppState.OK, identity_state=IdentityState.OK)

    async def _get_assistant(app_id):
        return _FakeAssistant()

    async def _report(task, started_at, end_time, status, *, app_name, trace_id):
        captured["app_name"] = app_name
        captured["trace_id"] = trace_id

    async def _finalize(*args, **kwargs):
        captured["finalize"] = kwargs

    monkeypatch.setattr(assistant_runner.ScheduledTaskDao, "aget", staticmethod(_aget))
    monkeypatch.setattr(assistant_runner.trigger_guard, "recheck_task", _recheck)
    monkeypatch.setattr(assistant_runner.AssistantDao, "aget_one_assistant", staticmethod(_get_assistant))
    monkeypatch.setattr(assistant_runner, "_report_telemetry", _report)
    monkeypatch.setattr(assistant_runner.run_service, "_finalize", _finalize)

    import bisheng.api.services.assistant_agent as assistant_agent_module

    monkeypatch.setattr(assistant_agent_module, "AssistantAgent", _FakeAgent)
    return captured


async def test_execute_assistant_run_uses_app_name_and_run_trace(patched_runner):
    ok = await assistant_runner._execute_assistant_run_async(task_id=1, trace_id="run-trace")

    assert ok is True
    assert patched_runner["app_name"] == "customer-assistant", "监控应显示应用名，不是任务名"
    assert patched_runner["trace_id"] == "run-trace", "遥测必须用本轮租约 trace"
    assert patched_runner["finalize"]["token"] == "run-trace"


async def test_report_telemetry_builds_application_process_event(monkeypatch):
    captured = {}

    async def _log_event(**kwargs):
        captured.update(kwargs)

    monkeypatch.setattr(assistant_runner.telemetry_service, "log_event", _log_event)

    await assistant_runner._report_telemetry(
        _task(),
        started_at=100.0,
        end_time=103.5,
        status=assistant_runner.RunStatus.SUCCESS,
        app_name="customer-assistant",
        trace_id="run-trace",
    )

    assert captured["trace_id"] == "run-trace"
    assert captured["event_data"].app_name == "customer-assistant"
    assert captured["event_data"].app_id == "1001"
    assert captured["event_data"].process_time == 3500
