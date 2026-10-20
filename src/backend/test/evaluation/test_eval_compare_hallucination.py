"""F067: 幻觉率 — content-level Σcontradicted ÷ Σtotal (NOT per-question mean),
contradicted ⊆ FP without double-counting, N/A on a judge that predates the 4th
bucket, and the 4th-bucket instruction folded into BOTH judge prompt paths."""

import json
import math

import pytest

from bisheng.evaluation.domain.services.answer_correctness import (
    _KEY_MAP,
    build_judge_prompt_template,
    compute_answer_correctness,
)
from bisheng.evaluation.domain.services.evaluation_compare_report import (
    compute_hallucination_rate,
)


class TestHallucinationAggregation:
    def test_sigma_over_sigma_not_per_question_mean(self):
        # q1: 1 contradicting out of 5 answer statements; q2: 0 out of 2.
        # Σ/Σ = 1/7 ≈ 0.1429; a per-question mean would be (0.2 + 0.0) / 2 = 0.1.
        rows = [
            {"statements_contradicted_num": 1, "statements_num_answer": 5},
            {"statements_contradicted_num": 0, "statements_num_answer": 2},
        ]
        assert compute_hallucination_rate(rows) == pytest.approx(1 / 7)
        assert compute_hallucination_rate(rows) != pytest.approx(0.1)

    def test_contradicted_does_not_double_count_against_total(self):
        # 2 overlap + 3 answer-only = 5 answer statements total; 1 of them contradicts.
        row = {"statements_contradicted_num": 1, "statements_num_answer": 5}
        assert compute_hallucination_rate([row]) == pytest.approx(0.2)

    def test_zero_contradictions_is_zero_not_na(self):
        row = {"statements_contradicted_num": 0, "statements_num_answer": 4}
        assert compute_hallucination_rate([row]) == 0.0

    def test_na_when_no_counts_present(self):
        # legacy CSV: columns absent -> None on every row
        rows = [{"statements_contradicted_num": None, "statements_num_answer": None}]
        assert compute_hallucination_rate(rows) is None

    def test_skips_rows_missing_counts(self):
        rows = [
            {"statements_contradicted_num": None, "statements_num_answer": None},
            {"statements_contradicted_num": 2, "statements_num_answer": 10},
        ]
        assert compute_hallucination_rate(rows) == pytest.approx(0.2)

    def test_empty_rows_is_na(self):
        assert compute_hallucination_rate([]) is None


# ---------------------------------------------------------------------------
# Judge (answer_correctness): the 4th bucket must be appended to BOTH prompt paths
# ---------------------------------------------------------------------------

_CONTRA_KEY = _KEY_MAP["CONTRA"]


class _Gen:
    def __init__(self, text: str):
        self.text = text


class _Result:
    def __init__(self, texts: list[str]):
        self.generations = [[_Gen(t)] for t in texts]


class _FakeLLM:
    """Minimal stand-in returning pre-baked judge output, one per batch."""

    def __init__(self, texts: list[str]):
        self._texts = texts

    def generate(self, _batches):
        return _Result(self._texts)


_MISSING = object()


def _judge_json(overlap, answer_only, gt_only, contra=_MISSING) -> str:
    obj = {
        _KEY_MAP["TP"]: overlap,
        _KEY_MAP["FP"]: answer_only,
        _KEY_MAP["FN"]: gt_only,
    }
    if contra is not _MISSING:
        obj[_CONTRA_KEY] = contra
    return json.dumps([obj])


def _render_prompt(human_prompt: str = "") -> str:
    template = build_judge_prompt_template(human_prompt)
    return template.format(question="q", answer="a", ground_truth="g").content


class TestJudgePromptFourthBucket:
    def test_default_prompt_gets_fourth_bucket(self):
        assert _CONTRA_KEY in _render_prompt()

    def test_user_prompt_also_gets_fourth_bucket(self):
        user = "Extract. Question:{question} Answer:{answer} Ground truth:{ground_truth} Extracted statements:"
        assert _CONTRA_KEY in _render_prompt(user)


class TestJudgeOutputColumns:
    def test_contradicted_and_answer_total_counts(self):
        # 2 overlap + 3 answer-only = 5 answer statements; 1 contradicts
        text = _judge_json(["a", "b"], ["c", "d", "e"], ["f"], contra=["c"])
        out = compute_answer_correctness(_FakeLLM([text]), ["q"], ["ans"], [["gt"]])
        assert out["statements_contradicted_num"] == [1]
        assert out["statements_num_answer"] == [5]

    def test_old_judge_without_fourth_key_is_nan(self):
        # contradicted unknown -> NaN (excluded from the rate); total still counted
        text = _judge_json(["a"], ["b"], [])
        out = compute_answer_correctness(_FakeLLM([text]), ["q"], ["ans"], [["gt"]])
        assert math.isnan(out["statements_contradicted_num"][0])
        assert out["statements_num_answer"] == [2]
