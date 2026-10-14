"""F066: per-question performance metrics for evaluations.

Token usage is read back from the statistics telemetry index. Every evaluation
question runs under its own trace id, and the ``model_invoke`` telemetry events
of that run carry the same trace id:

- workflow: ``execute_workflow_get_answer`` generates a fresh run ``unique_id``
  per question, which the Celery worker sets as the telemetry trace id
  (``worker/workflow/tasks.py`` -> ``trace_id_var.set(unique_id)``);
- assistant: the evaluation sets an ``eval-{evaluation_id}-{index}`` trace id
  around each question (the assistant path runs in the API process).

Collection is best-effort by design: telemetry writes go through a thread pool,
so an event may not be indexed yet when we query. We retry briefly and degrade
to ``None`` (rendered as ``N/A``) rather than failing the evaluation.
"""

from __future__ import annotations

import math
import time
from typing import Any

from loguru import logger

from bisheng.core.search.elasticsearch.manager import get_es_connection_sync

# Event/index names shared with the telemetry writer and the existing reader
# (``telemetry_search/domain/services/call_logs.py``).
_MODEL_INVOKE_EVENT = "model_invoke"
_TELEMETRY_INDEX = "base_telemetry_events"

# The telemetry write is queued to a thread pool (fire-and-forget); poll briefly
# so a just-finished question is not reported as N/A.
_RETRY_ATTEMPTS = 4
_RETRY_INTERVAL_SECONDS = 0.5


def _prefixed(field: str) -> str:
    """Telemetry event_data keys carry the event-name prefix (e.g. ``model_invoke_total_token``)."""
    return f"{_MODEL_INVOKE_EVENT}_{field}"


def collect_token_usage(trace_id: str) -> dict[str, Any] | None:
    """Sum the ``model_invoke`` telemetry of one question into token/timing metrics.

    Returns ``None`` when nothing is available (telemetry unreachable, or no
    event indexed for this trace id) so callers can record ``N/A``.
    """
    if not trace_id:
        return None

    body = {
        "size": 0,
        "query": {
            "bool": {
                "must": [
                    {"term": {"event_type": _MODEL_INVOKE_EVENT}},
                    {"term": {"trace_id": trace_id}},
                ]
            }
        },
        "aggs": {
            "input_token": {"sum": {"field": f"event_data.{_prefixed('input_token')}"}},
            "output_token": {"sum": {"field": f"event_data.{_prefixed('output_token')}"}},
            "total_token": {"sum": {"field": f"event_data.{_prefixed('total_token')}"}},
            "first_token": {"avg": {"field": f"event_data.{_prefixed('first_token_cost_time')}"}},
            "calls": {"value_count": {"field": f"event_data.{_prefixed('total_token')}"}},
        },
    }

    for attempt in range(_RETRY_ATTEMPTS):
        try:
            es = get_es_connection_sync()
            resp = es.search(index=_TELEMETRY_INDEX, body=body)
            aggs = resp.get("aggregations") or {}
            calls = int(aggs.get("calls", {}).get("value", 0) or 0)
            if calls:
                return {
                    "input_token": int(aggs["input_token"]["value"] or 0),
                    "output_token": int(aggs["output_token"]["value"] or 0),
                    "total_token": int(aggs["total_token"]["value"] or 0),
                    "first_token_ms": _round_or_none(aggs["first_token"].get("value")),
                }
        except Exception as e:  # telemetry must never break an evaluation
            logger.warning("token usage collect failed trace_id={} attempt={}: {}", trace_id, attempt + 1, e)
            return None
        if attempt < _RETRY_ATTEMPTS - 1:
            time.sleep(_RETRY_INTERVAL_SECONDS)

    logger.warning("token usage not indexed trace_id={} after {} attempts", trace_id, _RETRY_ATTEMPTS)
    return None


def _round_or_none(value) -> int | None:
    if value is None:
        return None
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(v) else int(round(v))


def percentile(values: list[float], ratio: float) -> float:
    """Nearest-rank percentile over a non-empty sorted-able list of numbers."""
    ordered = sorted(values)
    if not ordered:
        raise ValueError("percentile requires at least one value")
    index = max(0, min(len(ordered) - 1, math.ceil(ratio * len(ordered)) - 1))
    return ordered[index]


def summarize_performance(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Aggregate per-question performance rows into the report's performance block.

    ``rows`` carry the keys written into the result CSV: ``duration_ms`` (int or
    None), ``input_token`` / ``output_token`` (int or None) and ``exec_status``
    (``success`` / ``failed``). Missing (``None``) samples are excluded from every
    aggregate; an aggregate with no valid sample is ``None`` (rendered as N/A).
    """
    total = len(rows)
    success = sum(1 for r in rows if r.get("exec_status") == "success")

    durations = [r["duration_ms"] for r in rows if _is_number(r.get("duration_ms"))]
    tokens = [r for r in rows if _is_number(r.get("input_token")) or _is_number(r.get("output_token"))]
    ttfts = [r["first_token_ms"] for r in rows if _is_number(r.get("first_token_ms"))]

    total_token = sum(int(r.get("input_token") or 0) + int(r.get("output_token") or 0) for r in tokens) if tokens else None
    duration_total_ms = sum(durations) if durations else None
    token_rate = (
        round(total_token / (duration_total_ms / 1000), 2)
        if total_token is not None and duration_total_ms
        else None
    )

    return {
        "question_count": total,
        "success_count": success,
        # 0 questions -> 0.0 is meaningless; report N/A instead.
        "success_rate": (success / total) if total else None,
        "avg_duration_ms": round(sum(durations) / len(durations)) if durations else None,
        "max_duration_ms": max(durations) if durations else None,
        "p95_duration_ms": round(percentile(durations, 0.95)) if durations else None,
        "total_token": total_token,
        "token_rate": token_rate,
        "avg_first_token_ms": round(sum(ttfts) / len(ttfts)) if ttfts else None,
    }


def _is_number(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and not math.isnan(float(value))
