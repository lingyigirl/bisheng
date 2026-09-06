"""F047 agent plaza — per-app access statistics (read-only).

Aggregates the F046 mid table ``mid_agent_run_dtl`` (one row per agent run,
5-minute sync lag). Metric semantics match F046's dashboard dataset:
visits = run count, success rate = success runs / total runs (0-1),
avg process time = mean of process_time (ms). Global aggregation — the mid
table carries no tenant_id (design 决策 2).

ES failures degrade to empty stats so the plaza list never blocks on the
statistics index (AC-22).
"""
from __future__ import annotations

import time

from loguru import logger

from bisheng.core.search.elasticsearch.manager import get_es_connection_sync
from bisheng.telemetry.domain.mid_table.agent_run import AgentRun

_INDEX = AgentRun._index_name


def _client(es_client):
    return es_client if es_client is not None else get_es_connection_sync()


def _now_ms() -> int:
    return int(time.time() * 1000)


def _empty() -> dict:
    return {
        'total_visits': 0,
        'recent_7d_visits': 0,
        'success_rate': 0.0,
        'avg_process_time': 0.0,
    }


def _parse(aggregations: dict, app_ids: list[int | str]) -> dict:
    total_buckets = {
        b['key']: b['doc_count']
        for b in (aggregations.get('total_by_app') or {}).get('buckets', [])
    }
    recent_buckets = {
        b['key']: b
        for b in (aggregations.get('recent') or {}).get('by_app', {}).get('buckets', [])
    }

    result: dict = {}
    for app_id in app_ids:
        key = str(app_id)
        total = total_buckets.get(key, 0)
        recent_bucket = recent_buckets.get(key)
        if recent_bucket is None:
            recent = 0
            success = 0
            avg_pt = 0.0
        else:
            recent = recent_bucket['doc_count']
            success = recent_bucket.get('success', {}).get('doc_count', 0)
            avg_pt = recent_bucket.get('avg_pt', {}).get('value') or 0.0
        result[app_id] = {
            'total_visits': total,
            'recent_7d_visits': recent,
            'success_rate': round(success / recent, 4) if recent else 0.0,
            'avg_process_time': avg_pt,
        }
    return result


def batch_stats(app_ids: list[int | str], days: int = 7, es_client=None) -> dict:
    """Per-app stats for a set of app ids in one ES round-trip (design 决策 3)."""
    if not app_ids:
        return {}
    try:
        body = {
            'size': 0,
            'query': {
                'bool': {'filter': [{'terms': {'app_id': [str(i) for i in app_ids]}}]},
            },
            'aggs': {
                'recent': {
                    'filter': {
                        'range': {'timestamp': {'gte': _now_ms() - days * 86400 * 1000}},
                    },
                    'aggs': {
                        'by_app': {
                            'terms': {'field': 'app_id', 'size': len(app_ids) + 10},
                            'aggs': {
                                'success': {'filter': {'term': {'status': 'success'}}},
                                'avg_pt': {'avg': {'field': 'process_time'}},
                            },
                        },
                    },
                },
                'total_by_app': {
                    'terms': {'field': 'app_id', 'size': len(app_ids) + 10},
                },
            },
        }
        resp = _client(es_client).search(index=_INDEX, body=body)
        return _parse(resp.get('aggregations', {}), app_ids)
    except Exception:
        logger.exception('plaza_stats: batch_stats failed for {} apps; degrading to empty', len(app_ids))
        return {}


def single_stats(app_id: int | str, days: int = 7, es_client=None) -> dict:
    """Single-app stats (preview drawer). Shares the batch semantics."""
    result = batch_stats([app_id], days=days, es_client=es_client)
    return result.get(app_id, _empty())
