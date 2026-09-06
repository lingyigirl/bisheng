"""F046: call-log queries over the raw telemetry events (agent runs + model calls)."""

import time
from typing import Any

from sqlmodel import select

from bisheng.common.constants.enums.telemetry import BaseTelemetryTypeEnum
from bisheng.common.dependencies.user_deps import UserPayload
from bisheng.core.context.tenant import get_current_tenant_id
from bisheng.core.database.manager import get_sync_db_session
from bisheng.core.search.elasticsearch.manager import get_es_connection_sync
from bisheng.database.models.tenant import UserTenant
from bisheng.telemetry_search.domain.schemas.call_logs import CallLogQuery

# Tenant user-id cache (5 min) — avoids a MySQL roundtrip per log page.
_tenant_user_cache: dict[int, tuple[float, list[int]]] = {}
_TENANT_CACHE_TTL = 300

MAX_RANGE_DAYS = 90
DEFAULT_RANGE_DAYS = 7

_AGENT_EVENT = BaseTelemetryTypeEnum.APPLICATION_PROCESS.value
_MODEL_EVENT = BaseTelemetryTypeEnum.MODEL_INVOKE.value


def _tenant_user_ids(tenant_id: int) -> list[int]:
    cached = _tenant_user_cache.get(tenant_id)
    if cached and time.time() - cached[0] < _TENANT_CACHE_TTL:
        return cached[1]
    with get_sync_db_session() as session:
        ids = session.exec(select(UserTenant.user_id).where(UserTenant.tenant_id == tenant_id)).all()
    ids = list(ids)
    _tenant_user_cache[tenant_id] = (time.time(), ids)
    return ids


def _build_query(query: CallLogQuery, tenant_id: int) -> dict:
    user_ids = _tenant_user_ids(tenant_id)
    must: list[dict] = []
    must.append({"terms": {"user_context.user_id": user_ids}})
    # ES date fields parse numeric bounds as epoch_millis regardless of the
    # declared format — multiply epoch seconds by 1000 (same as the mid-table
    # syncs do)
    must.append({"range": {"timestamp": {"gte": query.start_time * 1000, "lt": query.end_time * 1000}}})

    if query.record_type == "agent_run":
        must.append({"term": {"event_type": _AGENT_EVENT}})
    elif query.record_type == "model_call":
        must.append({"term": {"event_type": _MODEL_EVENT}})
    else:
        must.append(
            {"bool": {"should": [{"term": {"event_type": _AGENT_EVENT}}, {"term": {"event_type": _MODEL_EVENT}}]}}
        )

    if query.status:
        # status lives under different prefixed keys per event type
        must.append(
            {
                "bool": {
                    "should": [
                        {"term": {f"{_AGENT_EVENT}_status": query.status}},
                        {"term": {f"{_MODEL_EVENT}_status": query.status}},
                    ]
                }
            }
        )

    if query.app_id:
        must.append(
            {
                "bool": {
                    "should": [
                        {"term": {f"{_AGENT_EVENT}_app_id": query.app_id}},
                        {"term": {f"{_MODEL_EVENT}_app_id": query.app_id}},
                    ]
                }
            }
        )

    return {"query": {"bool": {"must": must}}}


def _map_hit(hit: dict) -> dict:
    source = hit["_source"]
    event_data = source.get("event_data") or {}
    user_context = source.get("user_context") or {}
    if source.get("event_type") == _AGENT_EVENT:
        return {
            "type": "agent_run",
            "app_id": event_data.get(f"{_AGENT_EVENT}_app_id", ""),
            "app_name": event_data.get(f"{_AGENT_EVENT}_app_name", ""),
            "app_type": event_data.get(f"{_AGENT_EVENT}_app_type", ""),
            "chat_id": event_data.get(f"{_AGENT_EVENT}_chat_id"),
            "user_id": user_context.get("user_id"),
            "user_name": user_context.get("user_name", ""),
            "status": event_data.get(f"{_AGENT_EVENT}_status"),
            "process_time": event_data.get(f"{_AGENT_EVENT}_process_time"),
            "time": source.get("timestamp"),
        }
    return {
        "type": "model_call",
        "model_id": event_data.get(f"{_MODEL_EVENT}_model_id"),
        "model_name": event_data.get(f"{_MODEL_EVENT}_model_name", ""),
        "app_id": event_data.get(f"{_MODEL_EVENT}_app_id", ""),
        "app_name": event_data.get(f"{_MODEL_EVENT}_app_name", ""),
        "user_id": user_context.get("user_id"),
        "user_name": user_context.get("user_name", ""),
        "status": event_data.get(f"{_MODEL_EVENT}_status"),
        "duration": (event_data.get(f"{_MODEL_EVENT}_end_time", 0) or 0)
        - (event_data.get(f"{_MODEL_EVENT}_start_time", 0) or 0),
        "first_token_cost_time": event_data.get(f"{_MODEL_EVENT}_first_token_cost_time"),
        "total_token": event_data.get(f"{_MODEL_EVENT}_total_token"),
        "time": source.get("timestamp"),
    }


class CallLogsService:
    @classmethod
    def query_call_logs(cls, login_user: UserPayload, query: CallLogQuery) -> dict:
        tenant_id = get_current_tenant_id()
        if tenant_id is None:
            tenant_id = getattr(login_user, "leaf_tenant_id", None) or 1
        # resolve default window (7 days) and enforce the 90-day cap
        query.start_time, query.end_time = query.resolve_window()
        body = _build_query(query, tenant_id)
        es = get_es_connection_sync()
        response = es.search(
            index="base_telemetry_events",
            body={
                **body,
                "sort": [{"timestamp": {"order": "desc"}}],
                "from": (query.page - 1) * query.page_size,
                "size": query.page_size,
                "track_total_hits": True,
            },
        )
        hits = response.get("hits", {})
        records = [_map_hit(hit) for hit in hits.get("hits", [])]
        total = hits.get("total", {})
        total_value = total.get("value", 0) if isinstance(total, dict) else total
        return {"records": records, "total": total_value, "page": query.page, "page_size": query.page_size}
