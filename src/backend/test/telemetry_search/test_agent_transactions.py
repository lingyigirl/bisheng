"""F052: agent-transaction list/detail query building, window caps, node merge."""

from datetime import datetime

import pytest
from fastapi import HTTPException

from bisheng.telemetry_search.domain.schemas.agent_transactions import AgentTransactionQuery
from bisheng.telemetry_search.domain.services.agent_transactions import (
    _build_list_query,
    _build_model_calls_query,
    _map_transaction_hit,
    _merge_node_rows,
)


def test_window_default_and_cap():
    q = AgentTransactionQuery()
    start, end = q.resolve_window()
    assert end - start == 7 * 86400

    with pytest.raises(HTTPException):
        AgentTransactionQuery(start_time=0, end_time=91 * 86400).resolve_window()

    with pytest.raises(HTTPException):
        AgentTransactionQuery(start_time=100, end_time=50).resolve_window()


def test_build_list_query_basics():
    q = AgentTransactionQuery(start_time=1000, end_time=2000)
    body = _build_list_query(q, tenant_id=1)
    must = body["query"]["bool"]["must"]
    assert any("user_context.user_id" in m.get("terms", {}) for m in must)
    assert any(m.get("term", {}).get("event_type") == "application_process" for m in must)
    range_clause = [m["range"]["timestamp"] for m in must if "range" in m][0]
    assert range_clause == {"gte": 1000 * 1000, "lt": 2000 * 1000}


def test_build_list_query_filters():
    q = AgentTransactionQuery(status="failed", app_id="flow-1", app_type="workflow", keyword="流水", start_time=1000, end_time=2000)
    body = _build_list_query(q, tenant_id=1)
    must = body["query"]["bool"]["must"]
    assert any(m.get("term") == {"application_process_status": "failed"} for m in must)
    assert any(m.get("term") == {"application_process_app_id": "flow-1"} for m in must)
    assert any(m.get("term") == {"application_process_app_type": "workflow"} for m in must)
    # keyword searches the prefixed input_snapshot text field (design 坑 1)
    assert any(m.get("match", {}).get("event_data.application_process_input_snapshot") == "流水" for m in must)


def test_map_transaction_hit():
    hit = {
        "_source": {
            "event_id": "evt-1",
            "trace_id": "chat-1_async_task_id",
            "timestamp": 1750000000,
            "event_type": "application_process",
            "user_context": {"user_id": 1, "user_name": "admin"},
            "event_data": {
                "application_process_app_id": "flow-1",
                "application_process_app_name": "流水分析智能体",
                "application_process_app_type": "workflow",
                "application_process_chat_id": "chat-1",
                "application_process_status": "success",
                "application_process_process_time": 1234,
                "application_process_input_snapshot": "银行流水文件:['bankflow.csv']",
                "application_process_start_time": 1000,
                "application_process_end_time": 2000,
            },
        }
    }
    rec = _map_transaction_hit(hit)
    assert rec["trace_id"] == "chat-1_async_task_id"
    assert rec["app_name"] == "流水分析智能体"
    assert rec["input_snapshot"] == "银行流水文件:['bankflow.csv']"
    assert rec["status"] == "success"
    assert rec["process_time"] == 1234


def test_map_transaction_hit_without_snapshot():
    hit = {
        "_source": {
            "trace_id": "t1",
            "event_type": "application_process",
            "user_context": {},
            "event_data": {},
        }
    }
    rec = _map_transaction_hit(hit)
    assert rec["input_snapshot"] is None
    assert rec["app_name"] == ""


def _row(node_id, event_type, status="running", reason=None, input_data=None, output_data=None, minute=0, exec_id=None):
    return {
        "node_id": node_id,
        "exec_id": exec_id,
        "node_name": f"node-{node_id}",
        "event_type": event_type,
        "status": status,
        "reason": reason,
        "input_data": input_data,
        "output_data": output_data,
        "create_time": datetime(2026, 9, 5, 10, minute, 0),
    }


def test_merge_start_end_pairs():
    rows = [_row("a", "start", minute=0), _row("a", "end", "success", output_data={"msg": "ok"}, minute=5)]
    nodes = _merge_node_rows(rows)
    assert len(nodes) == 1
    n = nodes[0]
    assert n["status"] == "success"
    assert n["duration"] == 300
    assert n["output_data"] == {"msg": "ok"}
    assert n["incomplete"] is False


def test_merge_start_only_incomplete():
    nodes = _merge_node_rows([_row("a", "start", minute=0)])
    assert nodes[0]["status"] == "running"
    assert nodes[0]["incomplete"] is True
    assert nodes[0]["end_time"] is None


def test_merge_failed_reason_kept():
    rows = [_row("a", "start"), _row("a", "end", "failed", reason="boom")]
    n = _merge_node_rows(rows)[0]
    assert n["status"] == "failed"
    assert n["reason"] == "boom"


def test_merge_loop_node_first_start_last_end():
    rows = [
        _row("a", "start", minute=0),
        _row("a", "end", "success", minute=1),
        _row("a", "start", minute=2),
        _row("a", "end", "success", minute=3),
    ]
    n = _merge_node_rows(rows)[0]
    # one entry per node id; start from the first pass, end from the last pass
    assert n["duration"] == 180
    assert n["incomplete"] is False


def test_merge_end_row_before_start_row():
    """uuid PK order is not insertion order — end may sort before start."""
    rows = [_row("a", "end", "success", minute=5), _row("a", "start", minute=0)]
    n = _merge_node_rows(rows)[0]
    assert n["status"] == "success"
    assert n["start_time"] is not None
    assert n["end_time"] is not None
    assert n["duration"] == 300
    assert n["incomplete"] is False


def test_merge_pairs_by_exec_id():
    """Loop node: two executions of the same node id -> two entries."""
    rows = [
        _row("a", "start", minute=0, exec_id="e1"),
        _row("a", "end", "success", minute=1, exec_id="e1"),
        _row("a", "start", minute=2, exec_id="e2"),
        _row("a", "end", "failed", "boom", minute=3, exec_id="e2"),
    ]
    nodes = _merge_node_rows(rows)
    assert len(nodes) == 2
    assert nodes[0]["exec_id"] == "e1"
    assert nodes[0]["duration"] == 60
    assert nodes[0]["incomplete"] is False
    assert nodes[1]["exec_id"] == "e2"
    assert nodes[1]["status"] == "failed"
    assert nodes[1]["reason"] == "boom"


def test_merge_empty():
    assert _merge_node_rows([]) == []


def test_build_model_calls_query():
    body = _build_model_calls_query(trace_id="chat-1_async_task_id", tenant_id=1)
    must = body["query"]["bool"]["must"]
    assert any(m.get("term") == {"event_type": "model_invoke"} for m in must)
    assert any(m.get("term") == {"trace_id": "chat-1_async_task_id"} for m in must)
    assert any("user_context.user_id" in m.get("terms", {}) for m in must)
