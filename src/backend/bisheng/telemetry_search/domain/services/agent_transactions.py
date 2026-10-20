"""F052: agent-transaction monitor service (transaction list + node detail).

List reads ``application_process`` events from the raw telemetry index (the
F046 tenant-user-collection scoping); node detail merges ``workflow_node_run``
rows; model calls are the run's ``model_invoke`` events joined by ``trace_id``.
All read-only — no writes to any F046 domain object (release-contract 表 1).
"""

import json
from collections import defaultdict
from collections.abc import Iterable
from datetime import datetime
from typing import Any

from fastapi import HTTPException
from loguru import logger
from sqlmodel import select

from bisheng.common.constants.enums.telemetry import BaseTelemetryTypeEnum
from bisheng.common.dependencies.user_deps import UserPayload
from bisheng.common.errcode.http_error import UnAuthorizedError
from bisheng.core.context.tenant import get_current_tenant_id
from bisheng.core.database import get_sync_db_session
from bisheng.core.search.elasticsearch.manager import get_es_connection_sync
from bisheng.database.models.message import ChatMessage
from bisheng.database.models.workflow_node_run import NodeRunDao
from bisheng.telemetry_search.domain.schemas.agent_transactions import (
    AgentTransactionExportQuery,
    AgentTransactionQuery,
)
from bisheng.telemetry_search.domain.services.call_logs import _tenant_user_ids

_AGENT_EVENT = BaseTelemetryTypeEnum.APPLICATION_PROCESS.value
_MODEL_EVENT = BaseTelemetryTypeEnum.MODEL_INVOKE.value

MAX_MODEL_CALLS = 200

# 6.3: a run's Q&A pair is persisted in `chatmessage` under the same chat_id.
# Question rows are unified across app types; answer categories differ:
# assistant=`answer`, workflow=`stream_msg`/`output_msg`/…, linsight=`task`,
# workstation=`agent_answer`. `input` is a prompt to the user, not an answer.
_EXPORT_QUESTION_CATEGORIES = ("question",)
_EXPORT_ANSWER_CATEGORIES = (
    "answer",
    "stream_msg",
    "output_msg",
    "output_with_input_msg",
    "output_with_choose_msg",
    "task",
    "agent_answer",
)
_EXPORT_ANSWER_CATEGORY_SET = set(_EXPORT_ANSWER_CATEGORIES)
_EXPORT_MESSAGE_CATEGORIES = _EXPORT_QUESTION_CATEGORIES + _EXPORT_ANSWER_CATEGORIES
# Questions can be written a little before the run starts (dialog input is
# persisted first), answers before the process event is emitted. Small slack
# either side keeps the pair inside the run window without stealing the next
# turn.
_EXPORT_WINDOW_SLACK_SECONDS = 300
_EXPORT_ANSWER_SLACK_SECONDS = 60
_EXPORT_QUERY_CHUNK_SIZE = 500


def _resolve_tenant_id(login_user: UserPayload) -> int:
    tenant_id = get_current_tenant_id()
    if tenant_id is None:
        tenant_id = getattr(login_user, "leaf_tenant_id", None) or 1
    return tenant_id


def _build_list_query(query: AgentTransactionQuery, tenant_id: int) -> dict:
    """ES bool for the transaction list; only application_process events."""
    user_ids = _tenant_user_ids(tenant_id)
    must: list[dict] = [{"terms": {"user_context.user_id": user_ids}}]
    # ES date fields parse numeric bounds as epoch_millis (same as call-logs)
    must.append({"range": {"timestamp": {"gte": query.start_time * 1000, "lt": query.end_time * 1000}}})
    must.append({"term": {"event_type": _AGENT_EVENT}})

    if query.status:
        must.append({"term": {f"{_AGENT_EVENT}_status": query.status}})
    if query.app_id:
        must.append({"term": {f"{_AGENT_EVENT}_app_id": query.app_id}})
    if query.app_type:
        must.append({"term": {f"{_AGENT_EVENT}_app_type": query.app_type}})
    if query.keyword:
        # event_data keys are prefixed (design 坑 1); match the text field,
        # and also let users locate a transaction by its serial number (trace_id)
        must.append(
            {
                "bool": {
                    "should": [
                        {"match": {f"event_data.{_AGENT_EVENT}_input_snapshot": query.keyword}},
                        {"term": {"trace_id": query.keyword}},
                    ],
                    "minimum_should_match": 1,
                }
            }
        )

    return {"query": {"bool": {"must": must}}}


def _map_transaction_hit(hit: dict) -> dict:
    source = hit["_source"]
    event_data = source.get("event_data") or {}
    user_context = source.get("user_context") or {}
    return {
        "trace_id": source.get("trace_id"),
        "app_id": event_data.get(f"{_AGENT_EVENT}_app_id", ""),
        "app_name": event_data.get(f"{_AGENT_EVENT}_app_name", ""),
        "app_type": event_data.get(f"{_AGENT_EVENT}_app_type", ""),
        "chat_id": event_data.get(f"{_AGENT_EVENT}_chat_id"),
        "user_id": user_context.get("user_id"),
        "user_name": user_context.get("user_name", ""),
        "status": event_data.get(f"{_AGENT_EVENT}_status"),
        "process_time": event_data.get(f"{_AGENT_EVENT}_process_time"),
        "input_snapshot": event_data.get(f"{_AGENT_EVENT}_input_snapshot"),
        "start_time": event_data.get(f"{_AGENT_EVENT}_start_time"),
        "end_time": event_data.get(f"{_AGENT_EVENT}_end_time"),
        "time": source.get("timestamp"),
    }


def _build_model_calls_query(trace_id: str, tenant_id: int) -> dict:
    user_ids = _tenant_user_ids(tenant_id)
    must: list[dict] = [
        {"terms": {"user_context.user_id": user_ids}},
        {"term": {"event_type": _MODEL_EVENT}},
        {"term": {"trace_id": trace_id}},
    ]
    return {"query": {"bool": {"must": must}}}


def _map_model_call_hit(hit: dict) -> dict:
    source = hit["_source"]
    event_data = source.get("event_data") or {}
    return {
        "model_id": event_data.get(f"{_MODEL_EVENT}_model_id"),
        "model_name": event_data.get(f"{_MODEL_EVENT}_model_name", ""),
        "status": event_data.get(f"{_MODEL_EVENT}_status"),
        "duration": (event_data.get(f"{_MODEL_EVENT}_end_time", 0) or 0)
        - (event_data.get(f"{_MODEL_EVENT}_start_time", 0) or 0),
        "first_token_cost_time": event_data.get(f"{_MODEL_EVENT}_first_token_cost_time"),
        "total_token": event_data.get(f"{_MODEL_EVENT}_total_token"),
        "time": source.get("timestamp"),
    }


def _fmt(dt) -> str | None:
    return dt.strftime("%Y-%m-%d %H:%M:%S") if dt else None


def _get(row: Any, name: str):
    """Field access for both ORM rows (attribute) and test fixtures (dict)."""
    return row.get(name) if isinstance(row, dict) else getattr(row, name, None)


def _merge_node_rows(rows: list[Any]) -> list[dict]:
    """Merge start/end event rows into one record per node execution.

    Pairing key = node_id + exec_id (the per-execution uuid). Loop nodes (the
    same node_id executed repeatedly) therefore produce one entry per pass.
    Rows written before the exec_id column fall back to pairing by node_id.
    A start without an end stays ``running`` + ``incomplete`` (spec 边界).
    """
    nodes: dict[str, dict] = {}
    order: list[str] = []
    for r in rows:
        node_id = _get(r, "node_id")
        exec_id = _get(r, "exec_id") or node_id
        key = f"{node_id}::{exec_id}"
        entry = nodes.setdefault(
            key,
            {
                "node_id": node_id,
                "exec_id": exec_id,
                "node_name": _get(r, "node_name"),
                "status": "running",
                "start_time": None,
                "end_time": None,
                "duration": None,
                "reason": None,
                "input_data": None,
                "output_data": None,
                "incomplete": True,
            },
        )
        if key not in order:
            order.append(key)
        if _get(r, "event_type") == "start":
            if entry["start_time"] is None:
                entry["start_time"] = _fmt(_get(r, "create_time"))
        else:
            entry["status"] = _get(r, "status")
            entry["end_time"] = _fmt(_get(r, "create_time"))
            entry["reason"] = _get(r, "reason")
            entry["input_data"] = _get(r, "input_data")
            entry["output_data"] = _get(r, "output_data")
            entry["incomplete"] = False
    # Duration in a post-pass: rows are ordered by DB clock (second precision)
    # and the uuid PK does NOT reflect insertion order, so start/end may arrive
    # in either sequence — compute after both are settled.
    for key in order:
        entry = nodes[key]
        if entry["start_time"] and entry["end_time"]:
            start = datetime.strptime(entry["start_time"], "%Y-%m-%d %H:%M:%S")
            end = datetime.strptime(entry["end_time"], "%Y-%m-%d %H:%M:%S")
            entry["duration"] = max(0, int((end - start).total_seconds()))
    return [nodes[key] for key in order]


def _map_export_trace(hit: dict) -> dict | None:
    """Map one run event to the {trace, chat, window} record used for pairing."""
    source = hit.get("_source") or {}
    event_data = source.get("event_data") or {}
    trace_id = source.get("trace_id")
    chat_id = event_data.get(f"{_AGENT_EVENT}_chat_id")
    start = event_data.get(f"{_AGENT_EVENT}_start_time")
    end = event_data.get(f"{_AGENT_EVENT}_end_time")
    # Without chat_id there is no way to reach the persisted Q&A pair.
    if not trace_id or not chat_id or start is None or end is None:
        return None
    return {"trace_id": trace_id, "chat_id": chat_id, "start": float(start), "end": float(end)}


def _row_epoch(row: Any) -> float | None:
    """Normalize a message row's create_time (datetime or epoch fixture) to seconds."""
    value = _get(row, "create_time")
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.timestamp()
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _json_object(text: str | None) -> dict | None:
    if not text:
        return None
    try:
        data = json.loads(text)
    except (TypeError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def extract_question_text(message: str | None) -> str:
    """Unwrap the user text from the ``question`` message envelope.

    Surfaces persist different shapes: workstation ``{"query", "files"}``,
    app chat ``{"data", "input"}``, and legacy plain strings (same set as
    ConversationExportService._extract_query_text).
    """
    raw = message or ""
    data = _json_object(raw)
    if not data:
        return raw
    for key in ("query", "input"):
        value = data.get(key)
        if isinstance(value, str) and value.strip():
            return value
    return raw


def extract_answer_text(message: str | None) -> str:
    """Unwrap the bot text from an answer message envelope.

    ``answer`` can be plain markdown or ``{"content": ...}``; ``agent_answer``
    and workflow ``stream_msg``/``output_msg`` carry ``{"msg": ...}`` with an
    optional ``events`` array. Only ``type == "text"`` events are kept —
    thinking/tool-call steps are not training targets.
    """
    raw = message or ""
    data = _json_object(raw)
    if not data:
        return raw
    content = data.get("content")
    if isinstance(content, str) and content.strip():
        return content
    body = data.get("msg")
    if isinstance(body, str) and body.strip():
        return body
    chunks = [
        event.get("content")
        for event in data.get("events") or []
        if isinstance(event, dict)
        and (event.get("type") or "").lower() == "text"
        and isinstance(event.get("content"), str)
    ]
    return "\n\n".join(chunk for chunk in chunks if chunk)


def _pick_qa(rows: list[Any], start: float, end: float) -> tuple[Any | None, Any | None]:
    """Pick the question/answer rows belonging to one run.

    The run window isolates the turn inside a long chat. Slack either side
    covers the question being persisted before the run starts and the answer
    before the process event is emitted. When the window has no question
    (clock skew / historic rows), fall back to the latest question before the
    run end so the answer still gets a prompt.
    """
    question_start = start - _EXPORT_WINDOW_SLACK_SECONDS
    # A question always precedes its answer, so never accept one after the run
    # end — that would pair the next turn's prompt with this run's answer.
    answer_end = end + _EXPORT_ANSWER_SLACK_SECONDS
    question = answer = fallback_question = None
    for row in rows:
        ts = _row_epoch(row)
        if ts is None or ts > answer_end:
            continue
        category = (_get(row, "category") or "").lower()
        if category == "question":
            if ts <= end and (fallback_question is None or ts >= _row_epoch(fallback_question)):
                fallback_question = row
            if question_start <= ts <= end and (question is None or ts >= _row_epoch(question)):
                question = row
        elif category in _EXPORT_ANSWER_CATEGORY_SET:
            if start <= ts <= answer_end and (answer is None or ts >= _row_epoch(answer)):
                answer = row
    return question or fallback_question, answer


def _passes_feedback(liked: int | None, mode: str) -> bool:
    if mode == "liked":
        return liked == 1
    if mode == "exclude_disliked":
        return liked != 2
    return True


def build_finetune_samples(
    traces: Iterable[dict],
    rows_by_chat: dict[str, list[Any]],
    feedback: str = "exclude_disliked",
) -> list[dict]:
    """Turn runs into single-turn fine-tuning samples.

    Format follows the instruction set accepted by the model-finetune upload:
    ``{"instruction", "input", "output"}``; ``input`` stays empty because the
    question is self-contained. Identical (question, answer) pairs are emitted
    once — repeated production questions otherwise skew the dataset.
    """
    samples: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for trace in traces:
        question, answer = _pick_qa(rows_by_chat.get(trace["chat_id"], []), trace["start"], trace["end"])
        if question is None or answer is None:
            continue
        if not _passes_feedback(_get(answer, "liked"), feedback):
            continue
        instruction = extract_question_text(_get(question, "message")).strip()
        output = extract_answer_text(_get(answer, "message")).strip()
        if not instruction or not output:
            continue
        key = (instruction, output)
        if key in seen:
            continue
        seen.add(key)
        samples.append({"instruction": instruction, "input": "", "output": output})
    return samples


def _load_chat_rows(
    chat_ids: Iterable[str],
    start: float,
    end: float,
    tenant_id: int,
) -> dict[str, list[Any]]:
    """Load Q&A message rows for the export window, grouped by chat_id.

    The window keeps the query bounded for long-lived chats; chunking keeps
    the ``IN`` list under driver placeholder limits.
    """
    ids = list(dict.fromkeys(chat_ids))
    rows_by_chat: dict[str, list[Any]] = defaultdict(list)
    if not ids:
        return rows_by_chat
    window_start = datetime.fromtimestamp(start - _EXPORT_WINDOW_SLACK_SECONDS)
    window_end = datetime.fromtimestamp(end + _EXPORT_WINDOW_SLACK_SECONDS)
    with get_sync_db_session() as session:
        for offset in range(0, len(ids), _EXPORT_QUERY_CHUNK_SIZE):
            chunk = ids[offset : offset + _EXPORT_QUERY_CHUNK_SIZE]
            rows = session.exec(
                select(ChatMessage)
                .where(ChatMessage.chat_id.in_(chunk))
                .where(ChatMessage.tenant_id == tenant_id)
                .where(ChatMessage.category.in_(_EXPORT_MESSAGE_CATEGORIES))
                .where(ChatMessage.create_time >= window_start)
                .where(ChatMessage.create_time <= window_end)
                .order_by(ChatMessage.id)
            ).all()
            for row in rows:
                if row.chat_id:
                    rows_by_chat[row.chat_id].append(row)
    return rows_by_chat


def _query_export_transactions(query: AgentTransactionExportQuery, tenant_id: int) -> list[dict]:
    """Query the filtered run set for export; refuse to truncate silently."""
    es = get_es_connection_sync()
    response = es.search(
        index="base_telemetry_events",
        body={
            **_build_list_query(query, tenant_id),
            "sort": [{"timestamp": {"order": "desc"}}],
            "collapse": {"field": "trace_id"},
            "from": 0,
            # One extra row tells us the cap was exceeded without a count query.
            "size": query.limit + 1,
        },
    )
    hits = response.get("hits", {}).get("hits", [])
    traces = [trace for hit in hits if (trace := _map_export_trace(hit))]
    if len(traces) > query.limit:
        raise HTTPException(
            status_code=400,
            detail=f"export exceeds {query.limit} runs; narrow the time range",
        )
    return traces


async def _check_log_permission(login_user: UserPayload) -> None:
    """AC-18: non-admins must hold the log web menu (same check as the audit APIs)."""
    if login_user.is_admin():
        return
    from bisheng.api.services.audit_log import AuditLogService

    if not await AuditLogService._user_has_log_web_menu(login_user):
        raise UnAuthorizedError.http_exception()


class AgentTransactionsService:
    @classmethod
    async def query_transactions(cls, login_user: UserPayload, query: AgentTransactionQuery) -> dict:
        await _check_log_permission(login_user)
        query.start_time, query.end_time = query.resolve_window()
        tenant_id = _resolve_tenant_id(login_user)
        body = _build_list_query(query, tenant_id)
        es = get_es_connection_sync()
        response = es.search(
            index="base_telemetry_events",
            body={
                **body,
                "sort": [{"timestamp": {"order": "desc"}}],
                # One round with a form input reports twice (execute_workflow +
                # continue_workflow share the trace) — collapse keeps the newest
                # (final) event per trace, so one transaction = one row (AC-09);
                # total becomes the distinct-transaction count.
                "collapse": {"field": "trace_id"},
                "from": (query.page - 1) * query.page_size,
                "size": query.page_size,
                "track_total_hits": True,
            },
        )
        hits = response.get("hits", {})
        records = [_map_transaction_hit(hit) for hit in hits.get("hits", [])]
        total = hits.get("total", {})
        total_value = total.get("value", 0) if isinstance(total, dict) else total
        return {"records": records, "total": total_value, "page": query.page, "page_size": query.page_size}

    @classmethod
    async def export_finetune_dataset(
        cls,
        login_user: UserPayload,
        query: AgentTransactionExportQuery,
    ) -> tuple[bytes, int]:
        """Export the filtered runs as an instruction-tuning JSON array.

        One sample per run (question -> answer), feedback-filtered. Returns the
        UTF-8 payload and sample count. Exceeding ``limit`` raises instead of
        truncating, so the caller narrows the time range.
        """
        await _check_log_permission(login_user)
        query.start_time, query.end_time = query.resolve_window()
        tenant_id = _resolve_tenant_id(login_user)
        traces = _query_export_transactions(query, tenant_id)
        rows_by_chat = _load_chat_rows(
            (trace["chat_id"] for trace in traces),
            query.start_time,
            query.end_time,
            tenant_id,
        )
        samples = build_finetune_samples(traces, rows_by_chat, query.feedback)
        logger.info(
            "agent finetune export user={} runs={} samples={} feedback={} window={}-{}",
            login_user.user_id,
            len(traces),
            len(samples),
            query.feedback,
            query.start_time,
            query.end_time,
        )
        payload = json.dumps(samples, ensure_ascii=False, indent=2).encode("utf-8")
        return payload, len(samples)

    @classmethod
    async def get_transaction_nodes(cls, login_user: UserPayload, trace_id: str) -> dict:
        await _check_log_permission(login_user)
        tenant_id = _resolve_tenant_id(login_user)
        user_ids = _tenant_user_ids(tenant_id)
        rows = NodeRunDao.get_run_nodes(trace_id, user_ids)
        return {"trace_id": trace_id, "nodes": _merge_node_rows(rows)}

    @classmethod
    async def get_transaction_model_calls(cls, login_user: UserPayload, trace_id: str) -> dict:
        await _check_log_permission(login_user)
        tenant_id = _resolve_tenant_id(login_user)
        body = _build_model_calls_query(trace_id, tenant_id)
        es = get_es_connection_sync()
        response = es.search(
            index="base_telemetry_events",
            body={**body, "sort": [{"timestamp": {"order": "asc"}}], "size": MAX_MODEL_CALLS},
        )
        hits = response.get("hits", {})
        records = [_map_model_call_hit(hit) for hit in hits.get("hits", [])]
        return {"trace_id": trace_id, "records": records}
