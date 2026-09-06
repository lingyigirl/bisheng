"""F054: dataset parsing — form_values validation, optional question column."""

import io

import pytest

from bisheng.evaluation.domain.services.evaluation_service import EvaluationService


def _csv(text: str) -> io.BytesIO:
    return io.BytesIO(text.encode("utf-8"))


def test_qa_csv_parses_as_before():
    data = EvaluationService.parse_csv(_csv("question,ground_truth\n太阳由什么驱动？,核聚变\n"))
    assert data == [{"question": "太阳由什么驱动？", "ground_truth": "核聚变"}]


def test_form_csv_with_form_values():
    data = EvaluationService.parse_csv(
        _csv(
            'question,ground_truth,form_values\n'
            '流水样例,预期结论,"{""银行流水文件（PDF/Excel/图片，可多份）"": [""tmp-dir/bank.csv""], ""贷款本金（万元，可选）"": ""500""}"\n'
        )
    )
    assert len(data) == 1
    assert data[0]["ground_truth"] == "预期结论"
    assert data[0]["form_values"] == {
        "银行流水文件（PDF/Excel/图片，可多份）": ["tmp-dir/bank.csv"],
        "贷款本金（万元，可选）": "500",
    }


def test_form_csv_question_column_optional():
    """AC-10: ground_truth required, question optional (missing -> empty string)."""
    data = EvaluationService.parse_csv(
        _csv('ground_truth,form_values\n预期结论,"{""表单项"": ""值""}"\n')
    )
    assert data == [{"question": "", "ground_truth": "预期结论", "form_values": {"表单项": "值"}}]


def test_invalid_form_values_json_reports_row_number():
    """AC-01: invalid JSON fails at create time with the offending row number."""
    with pytest.raises(ValueError) as exc:
        EvaluationService.parse_csv(
            _csv('ground_truth,form_values\n预期结论,"{bad json"\n')
        )
    assert "行" in str(exc.value) and "2" in str(exc.value)


def test_missing_ground_truth_rejected():
    with pytest.raises(ValueError):
        EvaluationService.parse_csv(_csv("question,form_values\n仅题目,{}\n"))


def test_empty_form_values_allowed():
    # an all-empty form_values column is dropped by the parser — absent means
    # "no form values", which the form branch treats as an empty payload
    data = EvaluationService.parse_csv(_csv('ground_truth,form_values\n预期结论,\n'))
    assert data[0]["ground_truth"] == "预期结论"
    assert data[0].get("form_values", "") in ("", None)
