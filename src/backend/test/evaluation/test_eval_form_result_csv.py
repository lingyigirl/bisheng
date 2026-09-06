"""F054 follow-up (2026-09-06): form-type result CSV shows the case summary in the question cell.

The judge still scores against the original (empty) question; the summary is
display-only so the downloaded result CSV identifies which form case each row
belongs to (spec §3 was amended accordingly).
"""

from bisheng.evaluation.domain.services.evaluation_service import (
    _inject_failed_rows,
    _overlay_form_question,
    format_form_values,
)


def test_format_form_values_readable_summary():
    assert format_form_values({"企业名称": "XX公司", "报表期": "2024Q1"}) == "企业名称: XX公司; 报表期: 2024Q1"


def test_format_form_values_list_joins_and_skips_empty():
    assert format_form_values({"文件": ["a.pdf", "b.csv"], "备注": ""}) == "文件: a.pdf, b.csv"
    assert format_form_values({"文件": [], "备注": None}) == ""
    assert format_form_values({}) == ""
    assert format_form_values("") == ""
    assert format_form_values(None) == ""


def test_overlay_fills_only_empty_question_rows():
    # real row_list carries the trailing total row (build_result_table contract)
    rows = [{"question": "", "answer": "a1"}, {"question": "q2", "answer": "a2"}, {"question": None}]
    csv_data = [
        {"question": "", "form_values": {"企业名称": "XX公司"}},
        {"question": "q2", "form_values": {"企业名称": "YY公司"}},
    ]
    _overlay_form_question(rows, csv_data)
    # form row: summary fills the empty cell
    assert rows[0]["question"] == "企业名称: XX公司"
    # Q&A row: real question wins even when form_values exist
    assert rows[1]["question"] == "q2"
    # total row untouched
    assert rows[2]["question"] is None


def test_overlay_leaves_form_row_without_values_empty():
    rows = [{"question": "", "answer": "a1"}, {"question": None}]
    csv_data = [{"question": "", "form_values": {}}]
    _overlay_form_question(rows, csv_data)
    assert rows[0]["question"] == ""
    assert rows[1]["question"] is None


def test_inject_failed_form_row_gets_summary_and_na_metrics():
    total_row = {"question": None}
    row_list = [total_row]
    _inject_failed_rows(
        row_list,
        [
            {
                "question": "",
                "ground_truth": "g",
                "answer": "[执行失败] boom",
                "form_values": {"企业名称": "YY公司"},
            }
        ],
    )
    injected = row_list[0]
    assert injected["question"] == "企业名称: YY公司"
    assert injected["ground_truth"] == "g"
    assert injected["answer"] == "[执行失败] boom"
    assert injected["recall"] == "N/A"
    assert injected["precision"] == "N/A"
    assert injected["F1"] == "N/A"
    # total row stays last
    assert row_list[-1] is total_row


def test_inject_failed_qa_row_keeps_question():
    total_row = {"question": None}
    row_list = [total_row]
    _inject_failed_rows(row_list, [{"question": "q1", "answer": "[执行失败] boom", "ground_truth": "g"}])
    assert row_list[0]["question"] == "q1"
    assert row_list[-1] is total_row
