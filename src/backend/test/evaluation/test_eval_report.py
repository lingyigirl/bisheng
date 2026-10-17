"""F066: evaluation report (xlsx) + the approval-side report block."""

import io

import pandas as pd
import pytest

from bisheng.approval.domain.services.approval_center_service import _build_evaluation_report_block
from bisheng.evaluation.domain.models.evaluation import Evaluation, EvaluationTaskStatus, ExecType
from bisheng.evaluation.domain.repositories.evaluation_repository import EvaluationRepository
from bisheng.evaluation.domain.services import evaluation_report
from bisheng.evaluation.domain.services.evaluation_report import (
    DETAIL_SHEET,
    SUMMARY_SHEET,
    _result_scores,
    _to_number,
    build_report_bytes,
    build_report_summary,
)


def _evaluation(**overrides) -> Evaluation:
    data = dict(
        id=7,
        user_id=1,
        exec_type=ExecType.WORKFLOW.value,
        unique_id="app-1",
        version=2,
        status=EvaluationTaskStatus.success.value,
        file_name="dataset.csv",
        result_score={"answer_f1": "62.35%", "answer_recall": "70.10%", "answer_precision": "58.20%"},
    )
    data.update(overrides)
    return Evaluation(**data)


def _read_sheets(content):
    """Read the workbook back verbatim: keep_default_na=False so the literal
    ``N/A`` cells are not coerced to NaN by pandas (the report writes the string)."""
    return pd.read_excel(io.BytesIO(content), sheet_name=None, keep_default_na=False)


def _rows():
    return [
        {
            "question": "q1",
            "ground_truth": "gt1",
            "answer": "a1",
            "statements_num_overlap": 2,
            "statements_num_answer_only": 0,
            "statements_num_gt_only": 1,
            "recall": "66.67%",
            "precision": "100.00%",
            "F1": "80.00%",
            "duration_ms": 1200,
            "input_token": 10,
            "output_token": 20,
            "first_token_ms": 300,
            "exec_status": "success",
        },
        {
            "question": None,
            "ground_truth": None,
            "answer": None,
            "duration_ms": None,
            "input_token": None,
            "output_token": None,
            "first_token_ms": None,
            "exec_status": None,
        },
    ]


# --------------------------------------------------------------------------- helpers


def test_to_number_parses_percent_na_and_plain():
    assert _to_number("48.49%") == pytest.approx(0.4849)
    assert _to_number("12") == 12
    assert _to_number("N/A") is None
    assert _to_number("nan%") is None
    assert _to_number("") is None
    assert _to_number(float("nan")) is None


def test_result_scores_parses_stored_percentages():
    scores = _result_scores(_evaluation())
    assert scores["answer_f1"] == pytest.approx(0.6235)
    assert scores["answer_recall"] == pytest.approx(0.7010)


def test_result_scores_handles_json_string():
    scores = _result_scores(_evaluation(result_score='{"answer_f1": "50%"}'))
    assert scores["answer_f1"] == pytest.approx(0.5)


# --------------------------------------------------------------------------- workbook


@pytest.fixture()
def _no_db_meta(monkeypatch):
    """Report generation must not need the DB for the app header in this test."""
    monkeypatch.setattr(evaluation_report, "_resolve_app_meta", lambda evaluation, app_name=None: ("My App", "workflow", "v2"))


def test_build_report_bytes_has_two_sheets_and_summary_metrics(_no_db_meta):
    evaluation = _evaluation()
    perf = {"question_count": 2, "success_rate": 0.5, "avg_duration_ms": 1200, "max_duration_ms": 1200,
            "p95_duration_ms": 1200, "total_token": 30, "token_rate": 25.0, "avg_first_token_ms": 300}
    content = build_report_bytes(evaluation, _rows(), perf, app_name="My App", user_name="tester")

    sheets = _read_sheets(content)
    assert set(sheets) == {SUMMARY_SHEET, DETAIL_SHEET}

    summary = sheets[SUMMARY_SHEET]
    values = dict(zip(summary["项目"], summary["值"]))
    assert values["应用名称"] == "My App"
    assert values["评测编号"] == "7"
    assert values["F1"] == "62.35%"
    assert values["请求成功率"] == "50.00%"
    assert values["平均 Token 速率"] == "25.0 token/s"

    detail = sheets[DETAIL_SHEET]
    assert list(detail.columns)[0] == "题目"
    assert detail.iloc[0]["题目"] == "q1"
    assert detail.iloc[0]["执行状态"] == "成功"


def test_build_report_bytes_tolerates_missing_performance(_no_db_meta):
    perf = {"question_count": 2, "success_rate": None, "avg_duration_ms": None, "max_duration_ms": None,
            "p95_duration_ms": None, "total_token": None, "token_rate": None, "avg_first_token_ms": None}
    content = build_report_bytes(_evaluation(), _rows(), perf, app_name="My App")
    summary = _read_sheets(content)[SUMMARY_SHEET]
    values = dict(zip(summary["项目"], summary["值"]))
    assert values["请求成功率"] == "N/A"
    assert values["Token 总量"] == "N/A"


# --------------------------------------------------------------------------- repository


def _seed_eval(session, **kwargs):
    ev = Evaluation(
        user_id=1,
        exec_type=ExecType.WORKFLOW.value,
        unique_id=kwargs.pop("unique_id", "app-1"),
        version=kwargs.pop("version", 2),
        status=kwargs.pop("status", EvaluationTaskStatus.success.value),
        is_delete=kwargs.pop("is_delete", 0),
        **kwargs,
    )
    session.add(ev)
    session.commit()
    session.refresh(ev)
    return ev


def test_get_latest_success_by_app_version_matches_version(sqlite_session_patch):
    _seed_eval(sqlite_session_patch, version=1)
    _seed_eval(sqlite_session_patch, version=2)
    found = EvaluationRepository.get_latest_success_by_app_version("app-1", 2)
    assert found is not None and found.version == 2


def test_get_latest_success_by_app_version_ignores_failed_and_deleted(sqlite_session_patch):
    _seed_eval(sqlite_session_patch, status=EvaluationTaskStatus.failed.value)
    _seed_eval(sqlite_session_patch, is_delete=1)
    assert EvaluationRepository.get_latest_success_by_app_version("app-1", 2) is None


def test_get_latest_success_by_app_version_without_version_matches_app(sqlite_session_patch):
    # assistants carry no version — the app id alone must be enough
    _seed_eval(sqlite_session_patch, version=None)
    assert EvaluationRepository.get_latest_success_by_app_version("app-1") is not None


# --------------------------------------------------------------------------- approval block


class _Instance:
    def __init__(self, payload, business_name="My App"):
        self.id = 99
        self.payload_snapshot = payload
        self.business_name = business_name


def test_report_block_unavailable_without_app_id():
    assert _build_evaluation_report_block(_Instance({"app_type": "workflow"})) == {"available": False}


def test_report_block_unavailable_without_successful_evaluation(monkeypatch):
    monkeypatch.setattr(
        EvaluationRepository, "get_latest_success_by_app_version", classmethod(lambda cls, *a, **k: None)
    )
    block = _build_evaluation_report_block(_Instance({"app_id": "app-1", "version_id": 2}))
    assert block == {"available": False}


def test_report_block_returns_summary(monkeypatch):
    seen = {}

    def _fake_lookup(cls, unique_id, version=None):
        seen["unique_id"] = unique_id
        seen["version"] = version
        return _evaluation()

    monkeypatch.setattr(EvaluationRepository, "get_latest_success_by_app_version", classmethod(_fake_lookup))
    monkeypatch.setattr(
        evaluation_report, "build_report_summary", lambda evaluation, app_name=None: {"available": True, "app_name": app_name}
    )
    block = _build_evaluation_report_block(_Instance({"app_id": "app-1", "version_id": 2}, business_name="App X"))
    assert block == {"available": True, "app_name": "App X"}
    assert seen == {"unique_id": "app-1", "version": 2}


def test_report_block_assistant_matches_without_version(monkeypatch):
    seen = {}

    def _fake_lookup(cls, unique_id, version=None):
        seen["version"] = version
        return _evaluation()

    monkeypatch.setattr(EvaluationRepository, "get_latest_success_by_app_version", classmethod(_fake_lookup))
    monkeypatch.setattr(evaluation_report, "build_report_summary", lambda evaluation, app_name=None: {"available": True})
    _build_evaluation_report_block(_Instance({"app_id": "asst-1", "version_id": 5, "app_type": "assistant"}))
    assert seen["version"] is None


def test_report_block_swallows_lookup_errors(monkeypatch):
    def _boom(cls, *a, **k):
        raise RuntimeError("db down")

    monkeypatch.setattr(EvaluationRepository, "get_latest_success_by_app_version", classmethod(_boom))
    # the approval page must keep working (spec AC-11/AC-12)
    assert _build_evaluation_report_block(_Instance({"app_id": "app-1"})) == {"available": False}


def test_build_report_summary_degrades_when_result_unreadable(monkeypatch):
    # a failed evaluation has no result CSV -> available False, no raise
    summary = build_report_summary(_evaluation(status=EvaluationTaskStatus.failed.value))
    assert summary == {"available": False}
