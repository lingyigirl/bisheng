"""F066: on-demand evaluation report (xlsx) + the approval-side summary block.

The report is generated when it is downloaded — nothing is persisted: the
per-question performance metrics live in the result CSV (see
``evaluation_service``) and the aggregates are recomputed here, so old
evaluations (whose CSV predates this feature) still produce a report with the
performance block showing N/A.
"""

from __future__ import annotations

import io
import math
from typing import Any

import pandas as pd
from fastapi import HTTPException
from loguru import logger

from bisheng.core.storage.minio.minio_manager import get_minio_storage_sync
from bisheng.evaluation.domain.models.evaluation import Evaluation, EvaluationTaskStatus, ExecType
from bisheng.evaluation.domain.services.evaluation_metrics import summarize_performance
from bisheng.utils import generate_uuid

SUMMARY_SHEET = "汇总"
DETAIL_SHEET = "逐题明细"

# Report object key prefix (dataset/result live under their own prefixes).
_REPORT_DIR = "evaluation/report"

# Result-CSV column -> Chinese header shown in the detail sheet.
_DETAIL_COLUMN_LABELS: list[tuple[str, str]] = [
    ("question", "题目"),
    ("ground_truth", "标准答案"),
    ("answer", "答案"),
    ("statements_num_overlap", "命中数"),
    ("statements_num_answer_only", "多答数"),
    ("statements_num_gt_only", "漏答数"),
    ("recall", "召回"),
    ("precision", "准确率"),  # 展示名统一为「准确率」，与汇总页一致
    ("F1", "F1"),
    ("duration_ms", "耗时(ms)"),
    ("input_token", "输入Token"),
    ("output_token", "输出Token"),
    ("first_token_ms", "首Token延迟(ms)"),
    ("exec_status", "执行状态"),
]

_STATUS_LABELS = {"success": "成功", "failed": "失败"}
_NA = "N/A"


def _to_number(value: Any) -> float | None:
    """Parse a result-CSV cell (number, ``"48.49%"``, ``"N/A"``/``"nan%"``/empty) into a number."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return None if math.isnan(float(value)) else float(value)
    text = str(value).strip()
    if not text or text.lower() in ("na", "n/a", "nan", "nan%", "none"):
        return None
    try:
        return float(text.rstrip("%")) / 100.0 if text.endswith("%") else float(text)
    except ValueError:
        return None


def load_result_rows(evaluation: Evaluation) -> list[dict[str, Any]]:
    """Read the per-question rows of a finished evaluation from its result CSV.

    Columns added by F066 are absent in older result files; missing columns are
    filled with ``None`` so the report degrades to N/A instead of failing.
    """
    if evaluation.status != EvaluationTaskStatus.success.value or not evaluation.result_file_path:
        raise HTTPException(status_code=400, detail="Evaluation not finished or failed")
    # Imported lazily: evaluation_service imports this module's siblings, keep the
    # module graph acyclic at import time.
    from bisheng.evaluation.domain.services.evaluation_service import EvaluationService

    raw = EvaluationService.read_csv_file(evaluation.result_file_path)
    if raw is None:
        raise HTTPException(status_code=400, detail="Evaluation result file not found")
    # utf-8-sig strips the BOM written since 2026-09-06 and still reads legacy files.
    df = pd.read_csv(io.BytesIO(raw.read() if hasattr(raw, "read") else raw), encoding="utf-8-sig")
    # The trailing total row produced by build_result_table is not a question.
    df = df.dropna(axis=0, how="all")

    rows: list[dict[str, Any]] = []
    for _, row in df.iterrows():
        record: dict[str, Any] = {}
        for column, _label in _DETAIL_COLUMN_LABELS:
            record[column] = row.get(column)
        # Numeric coercion for the aggregate input. Percent/exact-score cells are
        # already plain numbers here (unlike the compare path, which parses the
        # formatted result CSV).
        record["duration_ms"] = _to_number(record.get("duration_ms"))
        record["input_token"] = _to_number(record.get("input_token"))
        record["output_token"] = _to_number(record.get("output_token"))
        record["first_token_ms"] = _to_number(record.get("first_token_ms"))
        status = record.get("exec_status")
        record["exec_status"] = str(status).strip().lower() if status not in (None, "") and not _is_nan(status) else None
        rows.append(record)
    return rows


def _is_nan(value: Any) -> bool:
    return isinstance(value, float) and math.isnan(value)


def _resolve_app_meta(evaluation: Evaluation, app_name: str | None = None) -> tuple[str, str, str]:
    """Resolve ``(app_name, app_type_label, version_name)`` for the report header."""
    name = app_name
    version_name = ""
    try:
        from bisheng.database.models.assistant import AssistantDao
        from bisheng.database.models.flow import FlowDao
        from bisheng.database.models.flow_version import FlowVersionDao

        if evaluation.exec_type == ExecType.ASSISTANT.value:
            assistant = AssistantDao.get_assistant(evaluation.unique_id)
            name = name or (assistant.name if assistant else evaluation.unique_id)
            app_type_label = "assistant"
        else:
            flow = FlowDao.get_flow_by_id(evaluation.unique_id)
            name = name or (flow.name if flow else evaluation.unique_id)
            app_type_label = "workflow"
        if evaluation.version:
            versions = FlowVersionDao.get_list_by_ids(ids=[evaluation.version])
            version_name = versions[0].name if versions else str(evaluation.version)
    except Exception as e:  # a missing name must not break report generation
        logger.warning("evaluation report app meta resolve failed id={}: {}", evaluation.id, e)
        name = name or str(evaluation.unique_id)
        app_type_label = evaluation.exec_type or "--"
    return name or "--", app_type_label, version_name or "--"


def _summary_sheet_rows(
    evaluation: Evaluation,
    app_name: str | None,
    user_name: str | None,
    perf: dict[str, Any],
    scores: dict[str, Any],
) -> list[list[str]]:
    name, app_type_label, version_name = _resolve_app_meta(evaluation, app_name=app_name)
    created = evaluation.create_time.strftime("%Y-%m-%d %H:%M:%S") if evaluation.create_time else "--"

    def pct(value: float | None) -> str:
        return f"{value * 100:.2f}%" if isinstance(value, (int, float)) else _NA

    def num(value: Any, unit: str = "") -> str:
        return f"{value}{unit}" if isinstance(value, (int, float)) else _NA

    return [
        ["基础信息", ""],
        ["应用名称", name],
        ["应用类型", app_type_label],
        ["版本", version_name],
        ["评测编号", str(evaluation.id)],
        ["评测时间", created],
        ["数据集", evaluation.file_name or "--"],
        ["题目数量", str(perf.get("question_count") or 0)],
        ["评测人", user_name or "--"],
        ["", ""],
        ["效果指标", ""],
        ["F1", pct(scores.get("answer_f1"))],
        ["召回", pct(scores.get("answer_recall"))],
        ["准确率", pct(scores.get("answer_precision"))],
        ["", ""],
        ["性能指标", ""],
        ["请求成功率", pct(perf.get("success_rate"))],
        ["平均端到端耗时", num(perf.get("avg_duration_ms"), " ms")],
        ["最大端到端耗时", num(perf.get("max_duration_ms"), " ms")],
        ["P95 端到端耗时", num(perf.get("p95_duration_ms"), " ms")],
        ["Token 总量", num(perf.get("total_token"))],
        ["平均 Token 速率", num(perf.get("token_rate"), " token/s")],
        ["平均首 Token 延迟", num(perf.get("avg_first_token_ms"), " ms")],
        ["", ""],
        ["说明", "本报告仅供上线审批与验收参考，不构成放行判定。"],
    ]


def _detail_sheet_frame(rows: list[dict[str, Any]]) -> pd.DataFrame:
    data = []
    for row in rows:
        record = {}
        for column, label in _DETAIL_COLUMN_LABELS:
            value = row.get(column)
            if column == "exec_status":
                record[label] = _STATUS_LABELS.get(value or "", value or _NA)
            elif column in ("question", "ground_truth", "answer", "recall", "precision", "F1"):
                record[label] = _NA if value is None or _is_nan(value) or value == "" else value
            else:
                record[label] = value if isinstance(value, (int, float)) else _NA
        data.append(record)
    return pd.DataFrame(data, columns=[label for _c, label in _DETAIL_COLUMN_LABELS])


def _result_scores(evaluation: Evaluation) -> dict[str, Any]:
    """Effect scores stored on the evaluation row (``result_score`` is a JSON dict or string)."""
    score = evaluation.result_score
    if isinstance(score, str):
        import json

        try:
            score = json.loads(score)
        except Exception:
            return {}
    return {
        key: (_to_number((score or {}).get(key)) if not isinstance((score or {}).get(key), str)
              else _parse_percent((score or {}).get(key)))
        for key in ("answer_f1", "answer_recall", "answer_precision")
    }


def _parse_percent(value: str) -> float | None:
    text = str(value).strip()
    if not text or text.lower() in ("na", "n/a", "nan", "nan%"):
        return None
    try:
        return float(text.rstrip("%")) / 100.0 if text.endswith("%") else float(text)
    except ValueError:
        return None


def build_report_bytes(
    evaluation: Evaluation,
    rows: list[dict[str, Any]],
    perf: dict[str, Any],
    app_name: str | None = None,
    user_name: str | None = None,
) -> bytes:
    """Render the two-sheet xlsx workbook (汇总 + 逐题明细) into bytes."""
    summary = pd.DataFrame(
        _summary_sheet_rows(evaluation, app_name, user_name, perf, _result_scores(evaluation)),
        columns=["项目", "值"],
    )
    detail = _detail_sheet_frame(rows)

    buffer = io.BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        summary.to_excel(writer, sheet_name=SUMMARY_SHEET, index=False)
        detail.to_excel(writer, sheet_name=DETAIL_SHEET, index=False)
    return buffer.getvalue()


def build_and_upload_report(
    evaluation: Evaluation,
    app_name: str | None = None,
    user_name: str | None = None,
) -> dict[str, str]:
    """Generate the report for a finished evaluation, store it in MinIO and return its links.

    Returns ``{"url": <absolute presigned url>, "file_name": <suggested download name>}``.
    The presigned url keeps its host (``clear_host=False``) — both the platform and
    the workspace client normalize the host before downloading.
    """
    rows = load_result_rows(evaluation)
    perf = summarize_performance(rows)
    content = build_report_bytes(evaluation, rows, perf, app_name=app_name, user_name=user_name)

    minio_client = get_minio_storage_sync()
    object_name = f"{_REPORT_DIR}/{generate_uuid()}.xlsx"
    minio_client.put_object_sync(
        bucket_name=minio_client.bucket,
        object_name=object_name,
        file=content,
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    url = minio_client.get_share_link_sync(object_name, clear_host=False, expire_days=7)
    return {"url": url, "file_name": f"evaluation_report_{evaluation.id}.xlsx"}


def build_report_summary(evaluation: Evaluation, app_name: str | None = None) -> dict[str, Any]:
    """The compact, approval-detail view of a report.

    Never raises: any failure (result CSV unreadable, MinIO down) degrades to
    ``{"available": False}`` so the approval page keeps working (spec AC-11/AC-12).
    """
    try:
        rows = load_result_rows(evaluation)
        perf = summarize_performance(rows)
        scores = _result_scores(evaluation)
        link = build_and_upload_report(evaluation, app_name=app_name)
    except Exception as e:
        logger.warning("evaluation report summary failed id={}: {}", evaluation.id, e)
        return {"available": False}

    def pct(value: float | None) -> str | None:
        return f"{value * 100:.2f}%" if isinstance(value, (int, float)) else None

    return {
        "available": True,
        "evaluation_id": evaluation.id,
        "evaluated_at": evaluation.update_time.strftime("%Y-%m-%d %H:%M:%S") if evaluation.update_time else None,
        "data_set": evaluation.file_name or None,
        "question_count": perf.get("question_count"),
        "f1": pct(scores.get("answer_f1")),
        "recall": pct(scores.get("answer_recall")),
        "precision": pct(scores.get("answer_precision")),
        "success_rate": pct(perf.get("success_rate")),
        "avg_duration_ms": perf.get("avg_duration_ms"),
        "token_rate": perf.get("token_rate"),
        "download_url": link["url"],
        "download_file_name": link["file_name"],
    }
