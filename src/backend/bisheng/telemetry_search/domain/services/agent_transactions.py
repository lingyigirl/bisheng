"""F052: agent-transaction monitor service (transaction list + node detail).

List reads ``application_process`` events from the raw telemetry index (the
F046 tenant-user-collection scoping); node detail merges ``workflow_node_run``
rows; model calls are the run's ``model_invoke`` events joined by ``trace_id``.
All read-only — no writes to any F046 domain object (release-contract 表 1).
"""

import time
from datetime import datetime
from typing import Any, List

from bisheng.common.constants.enums.telemetry import BaseTelemetryTypeEnum
from bisheng.common.dependencies.user_deps import UserPayload
from bisheng.common.errcode.http_error import UnAuthorizedError
from bisheng.core.context.tenant import get_current_tenant_id
from bisheng.core.search.elasticsearch.manager import get_es_connection_sync
from bisheng.database.models.workflow_node_run import NodeRunDao
from bisheng.telemetry_search.domain.schemas.agent_transactions import AgentTransactionQuery
from bisheng.telemetry_search.domain.services.call_logs import _tenant_user_ids

_AGENT_EVENT = BaseTelemetryTypeEnum.APPLICATION_PROCESS.value
_MODEL_EVENT = BaseTelemetryTypeEnum.MODEL_INVOKE.value

MAX_MODEL_CALLS = 200


def _resolve_tenant_id(login_user: UserPayload) -> int:
    tenant_id = get_current_tenant_id()
    if tenant_id is None:
        tenant_id = getattr(login_user, "leaf_tenant_id", None) or 1
    return tenant_id


def _build_list_query(query: AgentTransactionQuery, tenant_id: int) -> dict:
    """ES bool for the transaction list; only application_process events."""
    user_ids = _tenant_user_ids(tenant_id)
    must: List[dict] = [{"terms": {"user_context.user_id": user_ids}}]
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
        # event_data keys are prefixed (design 坑 1); match the text field
        must.append({"match": {f"event_data.{_AGENT_EVENT}_input_snapshot": query.keyword}})

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
    must: List[dict] = [
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


def _merge_node_rows(rows: List[Any]) -> List[dict]:
    """Merge start/end event rows into one record per node execution.

    Pairing key = node_id + exec_id (the per-execution uuid). Loop nodes (the
    same node_id executed repeatedly) therefore produce one entry per pass.
    Rows written before the exec_id column fall back to pairing by node_id.
    A start without an end stays ``running`` + ``incomplete`` (spec 边界).
    """
    nodes: dict[str, dict] = {}
    order: List[str] = []
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
