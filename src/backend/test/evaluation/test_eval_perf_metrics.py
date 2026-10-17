"""F066: per-question performance metrics — aggregation, token collection, CSV columns."""

import pytest

from bisheng.evaluation.domain.services import evaluation_metrics
from bisheng.evaluation.domain.services.evaluation_metrics import (
    collect_token_usage,
    percentile,
    summarize_performance,
)
from bisheng.evaluation.domain.services.evaluation_service import (
    _attach_perf_columns,
    _inject_failed_rows,
)


def _row(duration_ms=None, input_token=None, output_token=None, first_token_ms=None, exec_status="success"):
    return {
        "duration_ms": duration_ms,
        "input_token": input_token,
        "output_token": output_token,
        "first_token_ms": first_token_ms,
        "exec_status": exec_status,
    }


# --------------------------------------------------------------------------- summarize


def test_summarize_success_rate_counts_failed_in_denominator():
    rows = [_row(exec_status="success"), _row(exec_status="failed"), _row(exec_status="success")]
    summary = summarize_performance(rows)
    assert summary["question_count"] == 3
    assert summary["success_count"] == 2
    assert summary["success_rate"] == pytest.approx(2 / 3)


def test_summarize_latency_avg_max_p95():
    rows = [_row(duration_ms=v) for v in (100, 200, 300, 400, 1000)]
    summary = summarize_performance(rows)
    assert summary["avg_duration_ms"] == 400
    assert summary["max_duration_ms"] == 1000
    # nearest-rank p95 over 5 samples -> the max
    assert summary["p95_duration_ms"] == 1000


def test_summarize_token_rate_is_tokens_per_second():
    rows = [_row(duration_ms=1000, input_token=10, output_token=30), _row(duration_ms=1000, input_token=20, output_token=40)]
    summary = summarize_performance(rows)
    assert summary["total_token"] == 100
    # 100 tokens over 2s
    assert summary["token_rate"] == pytest.approx(50.0)


def test_summarize_excludes_missing_samples():
    rows = [_row(duration_ms=100, input_token=5, output_token=5), _row(duration_ms=None)]
    summary = summarize_performance(rows)
    assert summary["avg_duration_ms"] == 100
    assert summary["total_token"] == 10
    # the question with no duration still counts toward the success rate
    assert summary["question_count"] == 2


def test_summarize_all_missing_is_na():
    summary = summarize_performance([_row(), _row(exec_status="failed")])
    assert summary["avg_duration_ms"] is None
    assert summary["max_duration_ms"] is None
    assert summary["p95_duration_ms"] is None
    assert summary["total_token"] is None
    assert summary["token_rate"] is None
    assert summary["avg_first_token_ms"] is None


def test_summarize_empty_rows_has_no_success_rate():
    assert summarize_performance([])["success_rate"] is None


def test_percentile_rejects_empty():
    with pytest.raises(ValueError):
        percentile([], 0.95)


# --------------------------------------------------------------------------- token collection


class _FakeEs:
    def __init__(self, aggregations):
        self._aggregations = aggregations
        self.calls = 0

    def search(self, index, body):
        self.calls += 1
        return {"aggregations": self._aggregations}


def _aggs(calls, inp, out, total, ttft):
    return {
        "calls": {"value": calls},
        "input_token": {"value": inp},
        "output_token": {"value": out},
        "total_token": {"value": total},
        "first_token": {"value": ttft},
    }


def test_collect_token_usage_sums_events(monkeypatch):
    monkeypatch.setattr(evaluation_metrics, "get_es_connection_sync", lambda: _FakeEs(_aggs(3, 12, 34, 46, 210.4)))
    usage = collect_token_usage("trace-1")
    assert usage == {"input_token": 12, "output_token": 34, "total_token": 46, "first_token_ms": 210}


def test_collect_token_usage_returns_none_when_not_indexed(monkeypatch):
    # value_count 0 -> nothing indexed yet; retries then gives up (sleep patched out)
    monkeypatch.setattr(evaluation_metrics, "get_es_connection_sync", lambda: _FakeEs(_aggs(0, 0, 0, 0, None)))
    monkeypatch.setattr(evaluation_metrics.time, "sleep", lambda _s: None)
    assert collect_token_usage("trace-2") is None


def test_collect_token_usage_swallows_es_error(monkeypatch):
    def _boom():
        raise RuntimeError("es down")

    monkeypatch.setattr(evaluation_metrics, "get_es_connection_sync", _boom)
    # a broken telemetry backend must never break an evaluation
    assert collect_token_usage("trace-3") is None


def test_collect_token_usage_empty_trace_id():
    assert collect_token_usage("") is None


# --------------------------------------------------------------------------- result CSV columns


def test_attach_perf_columns_aligns_with_rows_and_skips_total_row():
    row_list = [{"question": "q1"}, {"question": "q2"}, {"question": None}]
    csv_data = [
        _row(duration_ms=100, input_token=1, output_token=2, first_token_ms=9),
        _row(duration_ms=200, exec_status="failed"),
    ]
    _attach_perf_columns(row_list, csv_data)
    assert row_list[0]["duration_ms"] == 100
    assert row_list[0]["input_token"] == 1
    assert row_list[0]["first_token_ms"] == 9
    assert row_list[0]["exec_status"] == "success"
    # missing token metrics render as N/A, not None
    assert row_list[1]["duration_ms"] == 200
    assert row_list[1]["input_token"] == "N/A"
    assert row_list[1]["exec_status"] == "failed"
    # total row untouched
    assert "duration_ms" not in row_list[2]


def test_inject_failed_rows_carries_perf_cells():
    total_row = {"question": None}
    row_list = [total_row]
    _inject_failed_rows(
        row_list,
        [{"question": "q", "ground_truth": "g", "answer": "[执行失败] boom", "duration_ms": 321, "exec_status": "failed"}],
    )
    injected = row_list[0]
    assert injected["duration_ms"] == 321
    assert injected["input_token"] == "N/A"
    assert injected["exec_status"] == "failed"
    assert row_list[-1] is total_row
