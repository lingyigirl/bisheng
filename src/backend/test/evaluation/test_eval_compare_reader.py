"""F067: result-CSV reader for the compare report — skip the total row, parse
performance columns as plain numbers, and report success rate as N/A (not 0%)
when the CSV predates the ``exec_status`` column.
"""

import pytest

from bisheng.evaluation.domain.services.evaluation_compare_report import (
    compute_success_rate,
    parse_result_rows,
)


def _csv(rows: list[list[str]], header: list[str]) -> bytes:
    """Build a minimal result CSV (utf-8-sig, like the real upload)."""
    lines = [",".join(header)] + [",".join(r) for r in rows]
    return ("﻿" + "\n".join(lines)).encode("utf-8")


_HEADER = [
    "question",
    "ground_truth",
    "answer",
    "statements_num_gt_only",
    "statements_num_answer_only",
    "statements_num_overlap",
    "recall",
    "precision",
    "F1",
    "duration_ms",
    "input_token",
    "output_token",
    "first_token_ms",
    "exec_status",
]


class TestParseResultRows:
    def test_skips_trailing_total_row(self):
        # 2 questions + a trailing total row (empty question, non-empty percent cells)
        csv = _csv(
            [
                ["q1", "gt1", "a1", "1", "0", "2", "66.67%", "100.00%", "80.00%", "1200", "10", "20", "300", "success"],
                ["q2", "gt2", "a2", "0", "1", "1", "50.00%", "50.00%", "50.00%", "2200", "20", "30", "400", "success"],
                ["", "", "", "0.5", "0.5", "1.5", "58.34%", "75.00%", "65.00%", "", "", "", "", ""],
            ],
            _HEADER,
        )
        rows = parse_result_rows(csv)
        assert len(rows) == 2  # the total row is not a question
        assert [r["question"] for r in rows] == ["q1", "q2"]

    def test_percent_columns_parse_to_fractions(self):
        csv = _csv(
            [["q1", "g", "a", "1", "0", "2", "66.67%", "100.00%", "80.00%", "1200", "10", "20", "300", "success"]],
            _HEADER,
        )
        row = parse_result_rows(csv)[0]
        assert row["recall"] == pytest.approx(0.6667)
        assert row["precision"] == pytest.approx(1.0)
        assert row["F1"] == pytest.approx(0.8)

    def test_performance_columns_use_plain_numeric_parse(self):
        # A pure-numeric latency column (no failed rows) must NOT be divided by 100.
        csv = _csv(
            [["q1", "g", "a", "1", "0", "2", "66.67%", "100.00%", "80.00%", "3200", "1234", "567", "210", "success"]],
            _HEADER,
        )
        row = parse_result_rows(csv)[0]
        assert row["duration_ms"] == 3200
        assert row["input_token"] == 1234
        assert row["output_token"] == 567
        assert row["first_token_ms"] == 210

    def test_failed_row_performance_and_status(self):
        csv = _csv(
            [["q1", "g", "[执行失败] boom", "0", "0", "0", "N/A", "N/A", "N/A", "321", "N/A", "N/A", "N/A", "failed"]],
            _HEADER,
        )
        row = parse_result_rows(csv)[0]
        assert row["exec_status"] == "failed"
        assert row["duration_ms"] == 321
        assert row["input_token"] is None  # N/A -> None, excluded from aggregates
        assert row["F1"] is None

    def test_legacy_csv_without_exec_status_column(self):
        header = _HEADER[:-1]  # no exec_status column at all (pre-F066 file)
        csv = _csv(
            [["q1", "g", "a", "1", "0", "2", "66.67%", "100.00%", "80.00%"]],
            header,
        )
        row = parse_result_rows(csv)[0]
        assert row["exec_status"] is None
        assert row["duration_ms"] is None

    def test_hallucination_count_columns_absent_are_none(self):
        csv = _csv(
            [["q1", "g", "a", "1", "0", "2", "66.67%", "100.00%", "80.00%", "1200", "10", "20", "300", "success"]],
            _HEADER,
        )
        row = parse_result_rows(csv)[0]
        assert row["statements_contradicted_num"] is None
        assert row["statements_num_answer"] is None


class TestComputeSuccessRate:
    def test_success_rate_over_all_questions(self):
        rows = [{"exec_status": "success"}, {"exec_status": "failed"}, {"exec_status": "success"}]
        assert compute_success_rate(rows) == pytest.approx(2 / 3)

    def test_na_when_no_status_present(self):
        # legacy CSV: every row lacks exec_status -> N/A, never 0%
        rows = [{"exec_status": None}, {"exec_status": None}]
        assert compute_success_rate(rows) is None

    def test_empty_rows_is_na(self):
        assert compute_success_rate([]) is None
