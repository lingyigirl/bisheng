"""F052: agent-transaction list/detail query building, window caps, node merge."""

from datetime import datetime

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from bisheng.telemetry_search.domain.schemas.agent_transactions import (
    AgentTransactionExportQuery,
    AgentTransactionQuery,
)
from bisheng.telemetry_search.domain.services import agent_transactions as service_module
from bisheng.telemetry_search.domain.services.agent_transactions import (
    _build_list_query,
    _build_model_calls_query,
    _map_export_trace,
    _map_transaction_hit,
    _merge_node_rows,
    _passes_feedback,
    _pick_qa,
    _query_export_transactions,
    build_finetune_samples,
    extract_answer_text,
    extract_question_text,
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
    q = AgentTransactionQuery(
        status="failed", app_id="flow-1", app_type="workflow", keyword="流水", start_time=1000, end_time=2000
    )
    body = _build_list_query(q, tenant_id=1)
    must = body["query"]["bool"]["must"]
    assert any(m.get("term") == {"application_process_status": "failed"} for m in must)
    assert any(m.get("term") == {"application_process_app_id": "flow-1"} for m in must)
    assert any(m.get("term") == {"application_process_app_type": "workflow"} for m in must)
    # keyword searches the prefixed input_snapshot text field (design 坑 1)
    # AND the transaction serial number (trace_id)
    kw = [m["bool"] for m in must if "bool" in m]
    assert kw, "keyword should clause missing"
    should = kw[0]["should"]
    assert {"match": {"event_data.application_process_input_snapshot": "流水"}} in should
    assert {"term": {"trace_id": "流水"}} in should


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


# ---------------------------------------------------------------------------
# 6.3 fine-tuning export
# ---------------------------------------------------------------------------


def _msg(category, message, at, liked=0):
    return {
        "category": category,
        "message": message,
        "create_time": datetime.fromtimestamp(at),
        "liked": liked,
    }


def _trace(chat_id="c1", start=1000, end=2000):
    return {"trace_id": f"{chat_id}-{start}", "chat_id": chat_id, "start": start, "end": end}


def test_export_query_schema_defaults_and_limit():
    q = AgentTransactionExportQuery()
    assert q.feedback == "exclude_disliked"
    assert q.limit == 5000

    with pytest.raises(ValidationError):
        AgentTransactionExportQuery(limit=5001)
    with pytest.raises(ValidationError):
        AgentTransactionExportQuery(limit=0)
    with pytest.raises(ValidationError):
        AgentTransactionExportQuery(feedback="unknown")


def test_map_export_trace_requires_chat_id():
    hit = {
        "_source": {
            "trace_id": "t1",
            "event_data": {
                "application_process_chat_id": "c1",
                "application_process_start_time": 1000,
                "application_process_end_time": 2000,
            },
        }
    }
    assert _map_export_trace(hit) == {"trace_id": "t1", "chat_id": "c1", "start": 1000.0, "end": 2000.0}

    hit["_source"]["event_data"].pop("application_process_chat_id")
    assert _map_export_trace(hit) is None


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("你好", "你好"),
        ('{"query": "你好", "files": []}', "你好"),
        ('{"data": {"x": 1}, "input": "表单问题"}', "表单问题"),
        ('{"data": {"x": 1}}', '{"data": {"x": 1}}'),
        (None, ""),
    ],
)
def test_extract_question_text(raw, expected):
    assert extract_question_text(raw) == expected


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("纯文本答案", "纯文本答案"),
        ('{"content": "知识库答案"}', "知识库答案"),
        ('{"msg": "智能体答案", "events": []}', "智能体答案"),
        (
            '{"msg": "", "events": [{"type": "thinking", "content": "略"}, {"type": "text", "content": "事件答案"}]}',
            "事件答案",
        ),
        ('{"events": [{"type": "text", "content": "a"}, {"type": "text", "content": "b"}]}', "a\n\nb"),
        ("", ""),
    ],
)
def test_extract_answer_text(raw, expected):
    assert extract_answer_text(raw) == expected


def test_pick_qa_prefers_current_turn():
    rows = [
        _msg("question", "第一问", 500),
        _msg("answer", "第一答", 600),
        _msg("question", "第二问", 900),
        _msg("answer", "第二答", 1500),
    ]
    question, answer = _pick_qa(rows, 1000, 2000)
    assert question["message"] == "第二问"
    assert answer["message"] == "第二答"


def test_pick_qa_falls_back_to_question_before_end():
    rows = [_msg("question", "只有一个问题", 950), _msg("answer", "答案", 1500)]
    question, answer = _pick_qa(rows, 1500, 2000)
    assert question["message"] == "只有一个问题"
    assert answer["message"] == "答案"


def test_pick_qa_ignores_next_turn():
    rows = [
        _msg("question", "本问", 1000),
        _msg("answer", "下一轮答案", 2100),
        _msg("question", "下一问", 2050),
    ]
    question, answer = _pick_qa(rows, 1000, 2000)
    assert question["message"] == "本问"
    # answer slack is 60s -> 2100 belongs to the next turn
    assert answer is None


def test_pick_qa_ignores_next_turn_inside_answer_slack():
    rows = [
        _msg("question", "本问", 1000),
        _msg("answer", "本轮答案", 1990),
        # A short next run finishing within the 60s slack must not overwrite
        # this run's answer.
        _msg("question", "下一问", 2040),
        _msg("answer", "下一轮答案", 2050),
    ]
    question, answer = _pick_qa(rows, 1000, 2000)
    assert question["message"] == "本问"
    assert answer["message"] == "本轮答案"


def test_passes_feedback_modes():
    assert _passes_feedback(0, "all")
    assert _passes_feedback(2, "all")
    assert not _passes_feedback(0, "liked")
    assert _passes_feedback(1, "liked")
    assert _passes_feedback(0, "exclude_disliked")
    assert not _passes_feedback(2, "exclude_disliked")


def test_build_finetune_samples_filters_and_dedups():
    rows = {
        "c1": [_msg("question", "问题一", 1000), _msg("answer", '{"msg": "答案一"}', 1500, liked=1)],
        "c2": [_msg("question", "问题二", 1000), _msg("answer", "答案二", 1500, liked=2)],
        "c3": [_msg("question", "问题三", 1000), _msg("answer", "答案三", 1500, liked=0)],
    }
    traces = [
        _trace("c1"),
        _trace("c2"),
        _trace("c3"),
        # same turn seen twice -> emitted once
        _trace("c1"),
    ]

    samples = build_finetune_samples(traces, rows, "exclude_disliked")
    assert samples == [
        {"instruction": "问题一", "input": "", "output": "答案一"},
        {"instruction": "问题三", "input": "", "output": "答案三"},
    ]

    liked_only = build_finetune_samples(traces, rows, "liked")
    assert liked_only == [{"instruction": "问题一", "input": "", "output": "答案一"}]

    all_modes = build_finetune_samples(traces, rows, "all")
    assert [s["instruction"] for s in all_modes] == ["问题一", "问题二", "问题三"]


def test_build_finetune_samples_skips_incomplete_turns():
    rows = {"c1": [_msg("question", "只有问题", 1000)]}
    assert build_finetune_samples([_trace("c1")], rows) == []


class _FakeES:
    def __init__(self, hits):
        self._hits = hits

    def search(self, index, body):
        return {"hits": {"hits": self._hits}}


def _export_hit(index):
    return {
        "_source": {
            "trace_id": f"t{index}",
            "event_data": {
                "application_process_chat_id": f"c{index}",
                "application_process_start_time": 1000,
                "application_process_end_time": 2000,
            },
        }
    }


def test_export_query_rejects_over_limit(monkeypatch):
    monkeypatch.setattr(
        service_module,
        "get_es_connection_sync",
        lambda: _FakeES([_export_hit(i) for i in range(3)]),
    )
    query = AgentTransactionExportQuery(start_time=1000, end_time=2000, limit=2)
    with pytest.raises(HTTPException):
        _query_export_transactions(query, tenant_id=1)


def test_export_query_allows_at_limit(monkeypatch):
    monkeypatch.setattr(
        service_module,
        "get_es_connection_sync",
        lambda: _FakeES([_export_hit(i) for i in range(2)]),
    )
    query = AgentTransactionExportQuery(start_time=1000, end_time=2000, limit=2)
    assert len(_query_export_transactions(query, tenant_id=1)) == 2


def test_export_query_rejects_over_limit_when_extra_hit_unmappable(monkeypatch):
    unmappable = _export_hit(2)
    unmappable["_source"]["event_data"].pop("application_process_chat_id")
    monkeypatch.setattr(
        service_module,
        "get_es_connection_sync",
        lambda: _FakeES([_export_hit(0), _export_hit(1), unmappable]),
    )
    query = AgentTransactionExportQuery(start_time=1000, end_time=2000, limit=2)
    with pytest.raises(HTTPException):
        _query_export_transactions(query, tenant_id=1)
