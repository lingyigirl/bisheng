"""F067: conclusion engine — priority chain 准确率→幻觉率→成本→延迟 with
direction normalization (lower-is-better reversed) and per-type tolerance."""

import pytest

from bisheng.evaluation.domain.services.evaluation_compare_report import (
    CONCLUSION_CHAIN,
    decide_conclusion,
)


def _totals(v1: dict, v2: dict) -> tuple[dict, dict]:
    base = {key: None for key, *_ in CONCLUSION_CHAIN}
    return {**base, **v1}, {**base, **v2}


class TestConclusionPriority:
    def test_precision_decides_at_top_priority(self):
        v1, v2 = _totals({"precision": 0.60}, {"precision": 0.75})
        result = decide_conclusion(v1, v2)
        assert result["conclusion"] == "v2"
        assert result["analysis"]["decider"] == "precision"
        assert result["analysis"]["descent"] is False

    def test_precision_tie_descends_to_hallucination(self):
        # precision within tolerance -> descend; hallucination lower is better -> v1
        v1, v2 = _totals({"precision": 0.700, "hallucination": 0.05}, {"precision": 0.705, "hallucination": 0.12})
        result = decide_conclusion(v1, v2)
        assert result["conclusion"] == "v1"
        assert result["analysis"]["decider"] == "hallucination"
        assert result["analysis"]["descent"] is True

    def test_all_within_tolerance_is_unable(self):
        v1, v2 = _totals(
            {"precision": 0.700, "hallucination": 0.05, "token": 1000, "latency": 3000},
            {"precision": 0.705, "hallucination": 0.055, "token": 1000, "latency": 3010},
        )
        result = decide_conclusion(v1, v2)
        assert result["conclusion"] == "unable"
        assert result["analysis"]["decider"] is None

    def test_token_lower_is_better(self):
        # precision + hallucination tie -> descend to token (fewer tokens wins)
        v1, v2 = _totals(
            {"precision": 0.70, "hallucination": 0.05, "token": 800},
            {"precision": 0.70, "hallucination": 0.05, "token": 1500},
        )
        result = decide_conclusion(v1, v2)
        assert result["conclusion"] == "v1"
        assert result["analysis"]["decider"] == "token"
        assert result["analysis"]["descent"] is True

    def test_latency_lower_is_better_after_full_descent(self):
        v1, v2 = _totals(
            {"precision": 0.70, "hallucination": 0.05, "token": 1000, "latency": 5000},
            {"precision": 0.70, "hallucination": 0.05, "token": 1000, "latency": 2000},
        )
        result = decide_conclusion(v1, v2)
        assert result["conclusion"] == "v2"
        assert result["analysis"]["decider"] == "latency"
        assert result["analysis"]["descent"] is True


class TestConclusionEdgeCases:
    def test_skips_metrics_missing_a_side(self):
        # precision missing on v2 -> skip; hallucination decides
        v1, v2 = _totals({"precision": 0.70, "hallucination": 0.03}, {"hallucination": 0.20})
        result = decide_conclusion(v1, v2)
        assert result["conclusion"] == "v1"
        assert result["analysis"]["decider"] == "hallucination"

    def test_all_missing_is_unable(self):
        v1, v2 = _totals({}, {})
        assert decide_conclusion(v1, v2)["conclusion"] == "unable"

    def test_comparisons_carry_direction_note(self):
        v1, v2 = _totals({"precision": 0.60}, {"precision": 0.75})
        comps = {c["key"]: c for c in decide_conclusion(v1, v2)["analysis"]["comparisons"]}
        assert comps["precision"]["better"] == "v2"
        # precision: higher is better; the raw delta (v2 - v1) is positive
        assert comps["precision"]["delta"] == pytest.approx(0.15)


class TestDisjointProvenance:
    """The two aggregates of a metric must come from overlapping questions."""

    def test_unusable_top_metric_blocks_lower_priority_winner(self):
        # v0 only Q1 scored, v1 only Q2 scored -> accuracy/hallucination are
        # disjoint. Token/latency still differ, but must NOT crown a winner.
        v1, v2 = _totals(
            {"precision": 0.10, "hallucination": 0.02, "token": 30000, "latency": 400000},
            {"precision": 0.26, "hallucination": 0.05, "token": 31000, "latency": 450000},
        )
        result = decide_conclusion(v1, v2, {"precision", "hallucination"})
        assert result["conclusion"] == "unable"
        assert result["analysis"]["decider"] is None
        assert result["analysis"]["unusable_metrics"] == ["precision", "hallucination"]
        comps = {c["key"]: c for c in result["analysis"]["comparisons"]}
        assert comps["precision"]["unusable"] is True

    def test_unusable_metric_after_a_valid_decider_keeps_the_decision(self):
        # precision is comparable and decides first; a later unusable hallucination
        # cannot retroactively invalidate it.
        v1, v2 = _totals({"precision": 0.60, "hallucination": 0.02}, {"precision": 0.75, "hallucination": 0.05})
        result = decide_conclusion(v1, v2, {"hallucination"})
        assert result["conclusion"] == "v2"
        assert result["analysis"]["decider"] == "precision"
