"""F052-era NaN cleanup: result-table aggregation skips judge parse failures."""

import math

from bisheng.evaluation.domain.services.evaluation_service import build_result_table

COLUMNS = [
    ("question", "question", 1),
    ("ground_truths", "ground_truth", 1),
    ("answer", "answer", 1),
    ("statements_num_gt_only", "statements_num_gt_only", 2),
    ("statements_num_answer_only", "statements_num_answer_only", 2),
    ("statements_num_overlap", "statements_num_overlap", 2),
    ("answer_recall", "recall", 3),
    ("answer_precision", "precision", 3),
    ("answer_f1", "F1", 3),
]


def _result(recall, precision, f1, n=2):
    return {
        "question": ["q"] * n,
        "ground_truths": [["g"]] * n,
        "answer": ["a"] * n,
        "statements_num_gt_only": [0] * n,
        "statements_num_answer_only": [0] * n,
        "statements_num_overlap": [2] * n,
        "answer_recall": recall,
        "answer_precision": precision,
        "answer_f1": f1,
    }


def test_nan_rows_excluded_from_aggregate():
    rows, total = build_result_table(
        _result([1.0, float("nan")], [1.0, float("nan")], [1.0, float("nan")]), COLUMNS)
    # per-question rows: first valid 100.00%, second N/A
    assert rows[0]["recall"] == "100.00%"
    assert rows[1]["recall"] == "N/A"
    # total row (last) averages only the valid row
    assert rows[-1]["recall"] == "100.00%"
    assert total == {"answer_recall": "100.00%", "answer_precision": "100.00%", "answer_f1": "100.00%"}


def test_all_nan_rows_aggregate_to_na():
    nan = float("nan")
    rows, total = build_result_table(_result([nan, nan], [nan, nan], [nan, nan]), COLUMNS)
    assert rows[0]["recall"] == "N/A"
    assert rows[-1]["recall"] == "N/A"
    assert total == {"answer_recall": "N/A", "answer_precision": "N/A", "answer_f1": "N/A"}


def test_partial_nan_mixed_average():
    # one valid 50%, one nan -> total 50.00%
    rows, total = build_result_table(
        _result([0.5, float("nan")], [0.5, float("nan")], [0.5, float("nan")]), COLUMNS)
    assert rows[-1]["recall"] == "50.00%"
    assert total["answer_recall"] == "50.00%"
