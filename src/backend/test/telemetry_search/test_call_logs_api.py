"""F046: call-logs query building, window caps and record mapping."""

import pytest
from fastapi import HTTPException

from bisheng.telemetry_search.domain.schemas.call_logs import CallLogQuery
from bisheng.telemetry_search.domain.services.call_logs import _build_query, _map_hit


def test_window_default_and_cap():
    q = CallLogQuery()
    start, end = q.resolve_window()
    assert end - start == 7 * 86400

    with pytest.raises(HTTPException):
        CallLogQuery(start_time=0, end_time=91 * 86400).resolve_window()

    with pytest.raises(HTTPException):
        CallLogQuery(start_time=100, end_time=50).resolve_window()


def test_build_query_agent_run_with_status():
    q = CallLogQuery(record_type="agent_run", status="success", start_time=1000, end_time=2000)
    body = _build_query(q, tenant_id=1)
    must = body["query"]["bool"]["must"]
    event_types = [m["term"]["event_type"] for m in must if "event_type" in m.get("term", {})]
    assert event_types == ["application_process"]
    # status filter is a nested bool with both prefixed keys
    status_bool = [m["bool"] for m in must if "bool" in m]
    assert status_bool, "status filter missing"
    keys = [t["term"] for t in status_bool[0]["should"]]
    assert "application_process_status" in [list(k.keys())[0] for k in keys]
    # tenant user filter present
    assert any("user_context.user_id" in m.get("terms", {}) for m in must)
    # numeric bounds must be epoch millis (ES date-field numeric semantics)
    range_clause = [m["range"]["timestamp"] for m in must if "range" in m][0]
    assert range_clause == {"gte": 1000 * 1000, "lt": 2000 * 1000}


def test_map_agent_run_hit():
    hit = {
        "_source": {
            "event_type": "application_process",
            "timestamp": 1750000000,
            "user_context": {"user_id": 3, "user_name": "测试人员"},
            "event_data": {
                "application_process_app_id": "flow-1",
                "application_process_app_name": "会议纪要智能助手",
                "application_process_app_type": "workflow",
                "application_process_status": "success",
                "application_process_process_time": 1234,
            },
        }
    }
    record = _map_hit(hit)
    assert record["type"] == "agent_run"
    assert record["app_name"] == "会议纪要智能助手"
    assert record["status"] == "success"
    assert record["process_time"] == 1234


def test_map_model_call_hit():
    hit = {
        "_source": {
            "event_type": "model_invoke",
            "timestamp": 1750000000,
            "user_context": {"user_id": 3, "user_name": "测试人员"},
            "event_data": {
                "model_invoke_model_name": "deepseek-v4-flash",
                "model_invoke_status": "failed",
                "model_invoke_start_time": 1000,
                "model_invoke_end_time": 2000,
                "model_invoke_total_token": 100,
            },
        }
    }
    record = _map_hit(hit)
    assert record["type"] == "model_call"
    assert record["model_name"] == "deepseek-v4-flash"
    assert record["status"] == "failed"
    assert record["duration"] == 1000
    assert record["total_token"] == 100
