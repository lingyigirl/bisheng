"""F052: per-node execution records for agent-run transaction monitoring.

Each node execution writes two rows (start / end) from the workflow worker
callbacks (``worker/workflow/redis_callback.py``); the transaction-detail API
merges them per ``(unique_id, node_id)``. Kept out of ``chatmessage`` so the
existing chat-history APIs return exactly what they did before (spec AC-05).

``create_time`` uses the DB clock (``CURRENT_TIMESTAMP``) on purpose — never
Python ``datetime.now()``: worker TZ and MySQL TZ have drifted by 8h in real
environments (see 2026-09-05 ops notes).
"""
import json
from datetime import datetime
from typing import Any, Dict, List, Optional

from loguru import logger
from sqlalchemy import Column, DateTime, Integer, String, Text
from sqlmodel import Field, select, text

from bisheng.common.models.base import SQLModelSerializable
from bisheng.core.database import get_sync_db_session
from bisheng.core.database.dialect_helpers import JsonType
from bisheng.utils import generate_uuid

# AC-06: single node input/output record cap (64KB) — spec §2.1.
MAX_PAYLOAD_BYTES = 64 * 1024
MAX_STRING_CHARS = 8 * 1024
MAX_LIST_ITEMS = 100
MAX_DEPTH = 8


def _sanitize_payload(value: Any, _depth: int = 0) -> Any:
    """Make node input/output data safe and small to persist (AC-06).

    - bytes/bytearray -> "<bytes N>" summary (no binary into MySQL JSON)
    - long strings truncated at MAX_STRING_CHARS
    - lists/sets capped at MAX_LIST_ITEMS, recursion depth capped
    - anything else stringified with a hard fallback
    """
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if _depth > MAX_DEPTH:
        return "<max depth>"
    if isinstance(value, (bytes, bytearray, memoryview)):
        return f"<bytes {len(value)}>"
    if isinstance(value, str):
        return value[:MAX_STRING_CHARS] + ("…<truncated>" if len(value) > MAX_STRING_CHARS else "")
    if isinstance(value, dict):
        out: Dict[str, Any] = {}
        for k, v in value.items():
            key = str(k)[:256]
            try:
                out[key] = _sanitize_payload(v, _depth + 1)
            except Exception:
                out[key] = "<unserializable>"
        return out
    if isinstance(value, (list, tuple, set)):
        try:
            items = list(value)
        except Exception:
            return "<unserializable>"
        out = [_sanitize_payload(v, _depth + 1) for v in items[:MAX_LIST_ITEMS]]
        if len(items) > MAX_LIST_ITEMS:
            out.append(f"<{len(items) - MAX_LIST_ITEMS} more items>")
        return out
    try:
        return str(value)[:MAX_STRING_CHARS]
    except Exception:
        return "<unserializable>"


def _prepare_payload(value: Any) -> Optional[Dict[str, Any]]:
    """Sanitize + enforce the 64KB cap; returns None when there is no data."""
    if value is None:
        return None
    sanitized = _sanitize_payload(value)
    if isinstance(sanitized, dict):
        return sanitized
    return {"value": sanitized}


def _ensure_payload_size(payload: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Fallback marker when a sanitized payload still exceeds the 64KB cap.

    Sanitization already caps strings/lists, so this is a last-resort guard
    (AC-06). Returns a small valid-JSON marker instead of oversized data.
    """
    if payload is None:
        return None
    try:
        if len(json.dumps(payload, ensure_ascii=False)) <= MAX_PAYLOAD_BYTES:
            return payload
    except Exception:
        pass
    return {"_truncated": True, "reason": f"payload exceeded {MAX_PAYLOAD_BYTES} bytes"}


class WorkflowNodeRunBase(SQLModelSerializable):
    """One row per node event; start/end rows merge into a node record on read."""

    unique_id: str = Field(index=True, description="Run trace id (=RedisCallback.unique_id, {chat_id}_async_task_id)")
    exec_id: str = Field(index=True, description="Per-execution uuid of this node run (NodeStartData.unique_id) — pairs start/end")
    chat_id: str = Field(index=True, description="Chat session id")
    flow_id: str = Field(index=True, description="Agent (flow) id")
    node_id: str = Field(description="Node unique id within the flow")
    node_name: str = Field(default="", description="Node name snapshot")
    event_type: str = Field(default="start", max_length=8, description="start | end")
    status: str = Field(default="running", max_length=16, description="running | success | failed")
    reason: Optional[str] = Field(default=None, sa_column=Column(Text), description="Failure reason (end rows)")
    input_data: Optional[Dict[str, Any]] = Field(
        default=None, sa_column=Column(JsonType), description="Node input variables (end rows, AC-06 capped)"
    )
    output_data: Optional[Dict[str, Any]] = Field(
        default=None, sa_column=Column(JsonType), description="Node output (end rows, AC-06 capped)"
    )
    user_id: int = Field(index=True, description="User who triggered the run (tenant scoping key)")
    tenant_id: Optional[int] = Field(
        default=None,
        sa_column=Column(Integer, nullable=True, index=True, comment="F052: run tenant (redundant; reads scope by user_id)"),
    )
    create_time: datetime = Field(
        sa_column=Column(DateTime, nullable=False, index=True, server_default=text("CURRENT_TIMESTAMP")),
        description="DB clock (never Python datetime.now())",
    )


class WorkflowNodeRun(WorkflowNodeRunBase, table=True):
    id: str = Field(default_factory=generate_uuid, primary_key=True)


class NodeRunDao:
    """Write path (worker callbacks) + read path (transaction-detail API)."""

    @classmethod
    def insert_event(
        cls,
        *,
        unique_id: str,
        exec_id: str,
        chat_id: str,
        flow_id: str,
        node_id: str,
        node_name: str,
        event_type: str,
        status: str,
        user_id: int,
        tenant_id: Optional[int],
        reason: Optional[str] = None,
        input_data: Any = None,
        output_data: Any = None,
    ) -> None:
        """Best-effort single-row insert — a failure must never break the run (AC-04)."""
        try:
            row = WorkflowNodeRun(
                unique_id=unique_id,
                exec_id=exec_id,
                chat_id=chat_id,
                flow_id=flow_id,
                node_id=node_id,
                node_name=node_name,
                event_type=event_type,
                status=status,
                reason=(reason[:2000] if reason else None),
                input_data=_ensure_payload_size(_prepare_payload(input_data)),
                output_data=_ensure_payload_size(_prepare_payload(output_data)),
                user_id=user_id,
                tenant_id=tenant_id,
            )
            with get_sync_db_session() as session:
                session.add(row)
                session.commit()
        except Exception as e:  # noqa: BLE001 — AC-04: persist failure is non-critical to the run
            logger.warning("workflow_node_run insert failed node={} event={}: {}", node_id, event_type, e)

    @classmethod
    def get_run_nodes(cls, unique_id: str, user_ids: List[int]) -> List[WorkflowNodeRun]:
        """All node-event rows of one run, oldest first.

        Scoped by the F046-style tenant user collection (user_id IN ...) rather
        than the tenant_id column — design 决策 5: a user who moved tenants
        still sees their own historical runs. bypass_tenant_filter() because the
        C3 auto-listener would otherwise inject ``tenant_id = ctx`` on top of
        the explicit user predicate and hide cross-tenant rows from a global
        super admin (same reason as AuditLogDao).
        """
        from bisheng.core.context.tenant import bypass_tenant_filter

        with bypass_tenant_filter(), get_sync_db_session() as session:
            statement = (
                select(WorkflowNodeRun)
                .where(WorkflowNodeRun.unique_id == unique_id, WorkflowNodeRun.user_id.in_(user_ids))
                .order_by(WorkflowNodeRun.create_time.asc(), WorkflowNodeRun.id.asc())
            )
            return list(session.exec(statement).all())
