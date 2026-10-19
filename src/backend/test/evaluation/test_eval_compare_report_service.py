"""F067: report assembly service — version ordering, the 6-metric cards (no
pass/fail judgment), per-question all-6-metrics, title stats/failure reasons,
and owner/tenant access errors."""

from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from bisheng.evaluation.domain.services import evaluation_compare_report as report


def _row(
    q,
    *,
    recall=None,
    precision=None,
    f1=None,
    duration=None,
    in_tok=None,
    out_tok=None,
    status="success",
    contra=None,
    total=None,
    answer="a",
):
    return {
        "question": q,
        "ground_truth": "g",
        "answer": answer,
        "recall": recall,
        "precision": precision,
        "F1": f1,
        "duration_ms": duration,
        "input_token": in_tok,
        "output_token": out_tok,
        "first_token_ms": None,
        "exec_status": status,
        "statements_contradicted_num": contra,
        "statements_num_answer": total,
    }


def _side(version, name, rows, flow_data=None, app="App", ds="ds.csv"):
    return {
        "id": version,
        "version": version,
        "version_name": name,
        "app_name": app,
        "data_set": ds,
        "status": 3,
        "result_score": None,
        "description": "",
        "flow_data": flow_data,
        "rows": rows,
    }


def _flow(model_id=5, model_name="LLM1", system_prompt="S"):
    return {
        "nodes": [
            {
                "id": "n1",
                "data": {
                    "type": "llm",
                    "name": model_name,
                    "group_params": [
                        {
                            "params": [
                                {"key": "model_id", "value": model_id},
                                {"key": "system_prompt", "value": system_prompt},
                                {"key": "user_prompt", "value": "U"},
                            ]
                        }
                    ],
                },
            }
        ],
        "edges": [],
    }


_ROWS_V1 = [
    _row("q1", recall=0.5, precision=0.5, f1=0.5, duration=3000, in_tok=10, out_tok=20),
    _row("q2", recall=0.5, precision=0.5, f1=0.5, duration=3000, in_tok=10, out_tok=20),
]
_ROWS_V2 = [
    _row("q1", recall=0.9, precision=0.9, f1=0.9, duration=2000, in_tok=8, out_tok=12),
    _row("q2", recall=0.9, precision=0.9, f1=0.9, duration=2000, in_tok=8, out_tok=12),
]


class TestBuildReportData:
    def test_metrics_cover_six_and_do_not_judge_pass_fail(self):
        data = report.build_report_data(_side(1, "v1", _ROWS_V1), _side(2, "v2", _ROWS_V2))
        keys = [m["key"] for m in data["metrics"]]
        assert keys == ["precision", "f1", "recall", "hallucination", "token", "latency"]
        for m in data["metrics"]:
            # direction is data, the pass/fail judgment belongs to another feature (6.4)
            assert set(m) >= {"key", "label", "v1", "v2", "delta", "unit", "higher_is_better"}
            assert "passed" not in m and "verdict" not in m

    def test_precision_decides_with_direction_normalized_metrics(self):
        # Guards the producer<->chain key match: the top-priority metric is
        # "precision" (准确率). If the chain key drifts, the decision silently
        # falls through to a lower metric and the analysis mentions the wrong one.
        data = report.build_report_data(_side(1, "v1", _ROWS_V1), _side(2, "v2", _ROWS_V2))
        assert data["conclusion"] == "v2"
        assert "准确率" in data["analysis"]
        assert "打平后" not in data["analysis"]  # decided at top priority, no descent

    def test_analysis_summarizes_direction_without_repeating_card_values(self):
        data = report.build_report_data(_side(1, "旧版", _ROWS_V1), _side(2, "新版", _ROWS_V2))
        analysis = data["analysis"]
        # One line of direction summary instead of one line per metric — the six
        # values are already shown in the metric cards right below the analysis.
        assert "指标对比：6 项指标中" in analysis
        for label in ("准确率", "F1", "召回", "幻觉率", "成本(Token)", "延迟"):
            assert label in analysis
        # F1/recall are shown for reference only, the chain metrics carry the verdict
        assert "参与结论判定：准确率 → 幻觉率 → 成本(Token) → 延迟" in analysis
        assert "F1、召回仅供参考，不参与结论" in analysis
        # only the deciding metric's margin remains in 结论依据
        assert analysis.count("变化 ") == 1
        assert "· 准确率：" not in analysis

    def test_analysis_names_failed_question_and_reason(self):
        rows1 = [_row("q1", status="failed", answer="[执行失败] boom")]
        rows2 = [_row("q1", precision=None, f1=None, recall=None, duration=80, in_tok=5, out_tok=5)]
        data = report.build_report_data(_side(1, "旧版", rows1), _side(2, "新版", rows2))
        analysis = data["analysis"]
        assert "第 1 题 旧版 执行失败（[执行失败] boom）" in analysis
        # judge output incomplete on a successful run is a distinct N/A cause
        assert "第 1 题 新版 执行成功但判分结果不完整" in analysis
        assert "准确率" in analysis and "幻觉率" in analysis

    def test_analysis_warns_when_metrics_come_from_different_questions(self):
        rows1 = [_row("q1", precision=0.5, f1=0.5, recall=0.5, duration=1000, in_tok=1, out_tok=1)]
        rows2 = [_row("q1"), _row("q2", precision=0.9, f1=0.9, recall=0.9, duration=1000, in_tok=1, out_tok=1)]
        data = report.build_report_data(_side(1, "旧版", rows1), _side(2, "新版", rows2))
        analysis = data["analysis"]
        # Both aggregates exist but no question is shared: the report must NOT
        # hand a win to either version (previously token decided on 2 vs 4).
        assert data["conclusion"] == "unable"
        assert "不同题目" in analysis
        assert "无法判定优劣" in analysis
        assert "数据不可比" in analysis
        assert "跨题提示" in analysis
        assert "旧版来自第 1 题，新版来自第 2 题" in analysis

    def test_disjoint_provenance_reported_in_summary_and_cross_question_lines(self):
        rows1 = [_row("q1", precision=0.1, f1=0.1, recall=0.1, duration=1000, in_tok=1, out_tok=1)]
        rows2 = [_row("q2", precision=0.9, f1=0.9, recall=0.9, duration=1000, in_tok=1, out_tok=1)]
        data = report.build_report_data(_side(1, "旧版", rows1), _side(2, "新版", rows2))
        assert data["conclusion"] == "unable"
        analysis = data["analysis"]
        # the summary flags the disjoint metrics once; the cross-question section
        # names the exact questions behind each side
        assert "数据不可比" in analysis
        assert "两版数值来自不同题目" in analysis
        assert "旧版来自第 1 题，新版来自第 2 题" in analysis

    def test_analysis_descent_mentions_tie(self):
        rows1 = [_row("q1", precision=0.5, contra=2, total=10, duration=1000, in_tok=10, out_tok=10)]
        rows2 = [_row("q1", precision=0.5, contra=0, total=10, duration=1000, in_tok=10, out_tok=10)]
        data = report.build_report_data(_side(1, "旧版", rows1), _side(2, "新版", rows2))
        assert data["conclusion"] == "v2"
        assert "准确率打平后" in data["analysis"]
        assert "幻觉率更优" in data["analysis"]

    def test_per_question_carries_all_six_metrics_both_sides(self):
        data = report.build_report_data(_side(1, "v1", _ROWS_V1), _side(2, "v2", _ROWS_V2))
        assert len(data["per_question"]) == 2
        entry = data["per_question"][0]
        assert entry["question"] == "q1"
        for side_key in ("v1", "v2"):
            assert set(entry[side_key]) >= {
                "precision",
                "f1",
                "recall",
                "hallucination",
                "token",
                "latency",
                "exec_status",
            }
        assert entry["v1"]["latency"] == 3000
        assert entry["v2"]["token"] == 20  # 8 + 12

    def test_per_question_hallucination_rate_is_per_question(self):
        rows1 = [_row("q1", contra=1, total=4)]
        rows2 = [_row("q1", contra=0, total=5)]
        data = report.build_report_data(_side(1, "v1", rows1), _side(2, "v2", rows2))
        entry = data["per_question"][0]
        assert entry["v1"]["hallucination"] == pytest.approx(0.25)
        assert entry["v2"]["hallucination"] == 0.0

    def test_title_stats_and_failure_reason(self):
        rows1 = [
            _row("q1", recall=0.5, precision=0.5, f1=0.5, duration=1000),
            _row("q2", status="failed", answer="[执行失败] boom", duration=100),
        ]
        rows2 = [_row("q1", recall=0.5, precision=0.5, f1=0.5, duration=1000), _row("q2", duration=120)]
        data = report.build_report_data(_side(1, "v1", rows1), _side(2, "v2", rows2))
        meta1 = data["evaluations"][0]
        assert meta1["question_count"] == 2
        assert meta1["success_count"] == 1
        assert meta1["failed_count"] == 1
        assert meta1["success_rate"] == pytest.approx(0.5)
        failed = next(e for e in data["per_question"] if e["question"] == "q2")
        assert failed["failed"] is True
        assert "boom" in (failed["fail_reason"] or "")

    def test_object_bar_includes_app_and_dataset(self):
        data = report.build_report_data(
            _side(1, "v1", _ROWS_V1, flow_data=_flow(model_id=5)),
            _side(2, "v2", _ROWS_V2, flow_data=_flow(model_id=7)),
            model_names={5: "qwen-turbo", 7: "qwen-plus"},
        )
        bar = data["object_bar"]
        assert bar["app_name"] == "App"
        assert bar["data_set"] == "ds.csv"
        assert bar["change_kind"] == "model"
        assert bar["models"]["v2"] == ["qwen-plus"]

    def test_charts_group_three_effect_metrics_on_one_axis(self):
        data = report.build_report_data(_side(1, "v1", _ROWS_V1), _side(2, "v2", _ROWS_V2))
        bars = data["charts"]["grouped_bar"]
        assert [b["key"] for b in bars] == ["precision", "f1", "recall"]


class TestBuildCompareReportAccess:
    def _eval(self, *, id, version, unique_id):
        return SimpleNamespace(
            id=id,
            version=version,
            unique_id=unique_id,
            result_file_path="p",
            file_name="ds.csv",
            status=3,
            result_score=None,
            description="",
        )

    def test_missing_group_is_404(self, monkeypatch):
        monkeypatch.setattr(
            "bisheng.evaluation.domain.repositories.evaluation_repository.EvaluationRepository.get_compare_group",
            classmethod(lambda cls, gid, uid: []),
        )
        with pytest.raises(HTTPException) as exc:
            report.build_compare_report("g1", 1)
        assert exc.value.status_code == 404

    def test_agents_mismatch_is_400(self, monkeypatch):
        evals = [self._eval(id=1, version=1, unique_id="app-a"), self._eval(id=2, version=2, unique_id="app-b")]
        monkeypatch.setattr(
            "bisheng.evaluation.domain.repositories.evaluation_repository.EvaluationRepository.get_compare_group",
            classmethod(lambda cls, gid, uid: evals),
        )
        with pytest.raises(HTTPException) as exc:
            report.build_compare_report("g1", 1)
        assert exc.value.status_code == 400

    def test_orders_members_by_version_and_sets_group_id(self, monkeypatch):
        # repository returns the newer version first; the report must order v1=old, v2=new
        evals = [self._eval(id=2, version=2, unique_id="app"), self._eval(id=1, version=1, unique_id="app")]
        monkeypatch.setattr(
            "bisheng.evaluation.domain.repositories.evaluation_repository.EvaluationRepository.get_compare_group",
            classmethod(lambda cls, gid, uid: evals),
        )
        sides = {1: _side(1, "v1", _ROWS_V1), 2: _side(2, "v2", _ROWS_V2)}
        monkeypatch.setattr(report, "load_compare_side", lambda e: sides[e.version])
        data = report.build_compare_report("g1", 1)
        assert data["group_id"] == "g1"
        assert [e["version"] for e in data["evaluations"]] == [1, 2]
