"""F053: compare computation — CSV parsing, question alignment, deltas, conclusion."""

import math

import pandas as pd
import pytest

from bisheng.evaluation.domain.services.evaluation_service import (
    _align_questions,
    _build_comparison,
    _parse_percent,
)


def test_parse_percent_variants():
    assert _parse_percent("48.49%") == pytest.approx(0.4849)
    assert _parse_percent("100.00%") == pytest.approx(1.0)
    assert _parse_percent("N/A") is None
    assert _parse_percent("nan%") is None
    assert _parse_percent("") is None
    assert _parse_percent(0.5) == pytest.approx(0.5)


def _df(questions, recalls, precisions, f1s):
    return pd.DataFrame(
        {
            "question": questions,
            "recall": recalls,
            "precision": precisions,
            "F1": f1s,
        }
    )


def test_build_comparison_delta_and_conclusion():
    df1 = _df(["q1", "q2"], ["50.00%", "80.00%"], ["50.00%", "80.00%"], ["50.00%", "80.00%"])
    df2 = _df(["q1", "q2"], ["60.00%", "70.00%"], ["60.00%", "70.00%"], ["60.00%", "70.00%"])
    comparison = _build_comparison(df1, df2)
    totals = comparison["totals"]
    assert totals["f1"]["v1"] == pytest.approx(0.65)
    assert totals["f1"]["v2"] == pytest.approx(0.65)
    assert abs(totals["f1"]["delta"]) < 1e-9  # equal -> no conclusion yet
    assert totals["recall"]["v2"] == pytest.approx(0.65)
    # per-question verdicts: q1 v2 better, q2 v2 worse
    by_q = {row["question"]: row["verdict"] for row in comparison["per_question"]}
    assert by_q["q1"] == "better"
    assert by_q["q2"] == "worse"


def test_conclusion_prefers_f1_then_recall():
    # v2: F1 worse, recall better -> conclusion v1
    df1 = _df(["q1"], ["50.00%"], ["50.00%"], ["60.00%"])
    df2 = _df(["q1"], ["70.00%"], ["70.00%"], ["55.00%"])
    comparison = _build_comparison(df1, df2)
    assert comparison["conclusion"] == "v1"
    # v2: F1 equal (within tolerance), recall better -> v2
    df1 = _df(["q1"], ["50.00%"], ["50.00%"], ["60.00%"])
    df2 = _df(["q1"], ["60.00%"], ["60.00%"], ["60.005%"])  # delta 0.00005 < 0.01
    comparison = _build_comparison(df1, df2)
    assert comparison["conclusion"] == "v2"


def test_na_rows_excluded_and_flagged():
    df1 = _df(["q1", "q2"], ["50.00%", "N/A"], ["50.00%", "N/A"], ["50.00%", "N/A"])
    df2 = _df(["q1", "q2"], ["60.00%", "70.00%"], ["60.00%", "70.00%"], ["60.00%", "70.00%"])
    comparison = _build_comparison(df1, df2)
    # totals only from q1 (q1's v1=0.5, v2=0.6)
    assert comparison["totals"]["f1"]["v1"] == pytest.approx(0.5)
    assert comparison["totals"]["f1"]["v2"] == pytest.approx(0.6)
    by_q = {row["question"]: row for row in comparison["per_question"]}
    assert by_q["q2"]["verdict"] == "na"


def test_both_all_na_conclusion_unable():
    df1 = _df(["q1"], ["N/A"], ["N/A"], ["N/A"])
    df2 = _df(["q1"], ["N/A"], ["N/A"], ["N/A"])
    comparison = _build_comparison(df1, df2)
    assert comparison["conclusion"] == "unable"


def test_align_questions_by_text_fallback():
    q1 = ["题一", "题二", "题三"]
    q2 = ["题三", "题一", "新题"]
    pairs = _align_questions(q1, q2)
    assert pairs == [("题一", "题一"), ("题二", None), ("题三", "题三"), (None, "新题")]


def test_align_questions_skips_empty():
    q1 = ["题一", "", "题二"]
    q2 = ["题一", "题二"]
    pairs = _align_questions(q1, q2)
    assert pairs == [("题一", "题一"), ("题二", "题二")]


def test_align_questions_same_order():
    q1 = ["a", "b"]
    q2 = ["a", "b"]
    assert _align_questions(q1, q2) == [("a", "a"), ("b", "b")]


def test_compare_group_visibility_scoped_to_user(sqlite_session_patch):
    """AC-12/AC-13: a user cannot read another user's compare group."""
    from fastapi import HTTPException

    from bisheng.evaluation.domain.models.evaluation import Evaluation
    from bisheng.evaluation.domain.services.evaluation_service import EvaluationService

    class _User:
        user_id = 1

    class _Other:
        user_id = 2

    for v in (1, 2):
        sqlite_session_patch.add(Evaluation(user_id=1, unique_id="u1", exec_type="workflow",
                                            version=v, compare_group_id="grp1", status=3))
    sqlite_session_patch.commit()

    resp = EvaluationService.get_evaluation_compare(_User(), "grp1")
    assert resp.data["group_id"] == "grp1"

    with pytest.raises(HTTPException) as exc:
        EvaluationService.get_evaluation_compare(_Other(), "grp1")
    assert exc.value.status_code == 404
