"""F047 plaza stats tests (AC-19, AC-22).

Test-first: fail (ModuleNotFoundError) until plaza_stats exists.
The ES client is injected, so no middleware is needed.
"""
import pytest

from bisheng.workstation.domain.services import plaza_stats as stats_mod


def _bucket(key, count, success=None, avg=None):
    bucket = {'key': str(key), 'doc_count': count}
    if success is not None:
        bucket['success'] = {'doc_count': success}
    if avg is not None:
        bucket['avg_pt'] = {'value': avg}
    return bucket


def _response(app_buckets, recent_buckets):
    return {
        'aggregations': {
            'recent': {
                'by_app': {'buckets': [_bucket(k, c, s, a) for (k, c, s, a) in recent_buckets]},
            },
            'total_by_app': {
                'buckets': [_bucket(k, c) for (k, c, _s, _a) in app_buckets],
            },
        }
    }


class _FakeEs:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def search(self, **kwargs):
        self.calls.append(kwargs)
        return self.response


def test_batch_stats_aggregates_counts_and_rate():
    """AC-19: visits, success rate and avg process time per app."""
    es = _FakeEs(_response(
        app_buckets=[('10', 100, 80, 500.0), ('11', 5, 5, 1000.0)],
        recent_buckets=[('10', 20, 16, 400.0)],
    ))
    result = stats_mod.batch_stats([10, 11], days=7, es_client=es)

    assert result[10]['total_visits'] == 100
    assert result[10]['recent_7d_visits'] == 20
    assert result[10]['success_rate'] == pytest.approx(16 / 20)
    assert result[10]['avg_process_time'] == 400.0
    # 11 has no recent visits → zeros, no crash
    assert result[11]['recent_7d_visits'] == 0
    assert result[11]['success_rate'] == 0
    assert result[11]['avg_process_time'] == 0
    assert result[11]['total_visits'] == 5


def test_batch_stats_missing_app_gets_zeros():
    """AC-22: apps without any data are represented with zero stats."""
    es = _FakeEs(_response(app_buckets=[], recent_buckets=[]))
    result = stats_mod.batch_stats([99], days=7, es_client=es)
    assert result[99]['total_visits'] == 0
    assert result[99]['recent_7d_visits'] == 0
    assert result[99]['success_rate'] == 0
    assert result[99]['avg_process_time'] == 0


def test_batch_stats_applies_days_window_to_recent_agg():
    """AC-19: the recent-window aggregation carries a timestamp range filter."""
    es = _FakeEs(_response(app_buckets=[], recent_buckets=[]))
    stats_mod.batch_stats([10], days=7, es_client=es)
    query = es.calls[0]
    recent_agg = query['body']['aggs']['recent']
    assert 'range' in recent_agg['filter']


def test_batch_stats_es_failure_degrades_to_empty():
    """AC-22 / design 坑 #4: ES outage must not block the plaza list."""
    class _BrokenEs:
        def search(self, **kwargs):
            raise RuntimeError('connection refused')

    result = stats_mod.batch_stats([10], days=7, es_client=_BrokenEs())
    assert result == {}


def test_single_stats_returns_same_shape():
    """AC-09/AC-19: per-app stats share the batch shape."""
    es = _FakeEs(_response(
        app_buckets=[('10', 100, 80, 500.0)],
        recent_buckets=[('10', 20, 16, 400.0)],
    ))
    result = stats_mod.single_stats(10, days=7, es_client=es)
    assert result['total_visits'] == 100
    assert result['recent_7d_visits'] == 20
