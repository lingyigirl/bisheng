"""F046: workflow telemetry events must carry success/failed run status."""

from bisheng.worker.workflow import tasks as wf_tasks


def _fake_flow_info(_flow_id):
    return None


def test_execute_workflow_reports_success(monkeypatch):
    captured = {}

    def fake_log_event_sync(**kwargs):
        captured["data"] = kwargs["event_data"]

    monkeypatch.setattr(wf_tasks, "_execute_workflow", lambda *a, **k: None)
    monkeypatch.setattr(wf_tasks.WorkFlowService, "get_one_workflow_simple_info_sync", _fake_flow_info)
    monkeypatch.setattr(wf_tasks.telemetry_service, "log_event_sync", fake_log_event_sync)

    wf_tasks.execute_workflow.run("uid-1", "flow-1", "chat-1", 1)

    assert captured["data"].status.value == "success"
    assert captured["data"].process_time >= 0


def test_execute_workflow_reports_failed(monkeypatch):
    captured = {}

    def fake_log_event_sync(**kwargs):
        captured["data"] = kwargs["event_data"]

    def boom(*a, **k):
        raise RuntimeError("boom")

    monkeypatch.setattr(wf_tasks, "_execute_workflow", boom)
    monkeypatch.setattr(wf_tasks.WorkFlowService, "get_one_workflow_simple_info_sync", _fake_flow_info)
    monkeypatch.setattr(wf_tasks.telemetry_service, "log_event_sync", fake_log_event_sync)

    try:
        wf_tasks.execute_workflow.run("uid-2", "flow-2", "chat-2", 1)
    except RuntimeError:
        pass

    assert captured["data"].status.value == "failed"
