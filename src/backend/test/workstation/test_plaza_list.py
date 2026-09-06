"""F047 plaza list service tests (AC-01, AC-03, AC-05, AC-06, AC-07, AC-08, AC-20, AC-23).

Test-first: fail (AttributeError) until PlazaService.list_plaza exists.
Dependencies (get_all_flows / TagDao / stats) are stubbed at the boundary.
"""
import pytest

from bisheng.workstation.domain.services import plaza_service as svc


class _User:
    def __init__(self, user_id=1):
        self.user_id = user_id

    def is_admin(self):
        return False


def _app(id, flow_type=5, update='2026-09-01T10:00:00'):
    return {
        'id': id, 'name': f'App{id}', 'description': f'Desc{id}',
        'flow_type': flow_type, 'update_time': update,
        'tags': [], 'logo': '', 'user_name': 'owner',
    }


class _Env:
    """Stub environment capturing calls; visible = get_all_flows result."""

    def __init__(self, monkeypatch, visible):
        self.calls = []
        self.visible = visible
        self.tag_resources = set()
        self.stats = {}

        async def _get_all_flows(user, name, status, tag_id, flow_type, page=1, page_size=10,
                                 managed=False, skip_pagination=False, search_description=False,
                                 permission_id='use_app', cursor=None):
            self.calls.append(dict(
                name=name, status=status, tag_id=tag_id, flow_type=flow_type,
                permission_id=permission_id, search_description=search_description,
                skip_pagination=skip_pagination,
            ))
            return self.visible, False

        monkeypatch.setattr(svc.WorkFlowService, 'get_all_flows', staticmethod(_get_all_flows))
        monkeypatch.setattr(svc.TagDao, 'get_resources_by_tags_batch', lambda ids, types: [
            type('T', (), {'resource_id': r})() for r in sorted(self.tag_resources)
        ] if ids else [])
        monkeypatch.setattr(svc.plaza_stats, 'batch_stats', lambda app_ids, days=7, es_client=None: {
            i: self.stats.get(i, {'recent_7d_visits': 0, 'total_visits': 0, 'success_rate': 0.0, 'avg_process_time': 0.0})
            for i in app_ids
        })


def _run(env, **kwargs):
    import asyncio
    params = dict(page=1, limit=20)
    params.update(kwargs)
    return asyncio.run(svc.PlazaService.list_plaza(_User(), **params))


def test_list_delegates_view_app_and_online(monkeypatch):
    """AC-01/AC-23: visibility = view_app + ONLINE, keyword covers description."""
    env = _Env(monkeypatch, [_app(1)])
    _run(env, keyword='App')
    call = env.calls[0]
    assert call['permission_id'] == 'view_app'
    assert call['status'] == 2  # FlowStatus.ONLINE
    assert call['search_description'] is True
    assert call['skip_pagination'] is True


def test_flow_types_multi_filter(monkeypatch):
    """AC-05: multi-select app types filter the result."""
    env = _Env(monkeypatch, [_app(1, flow_type=5), _app(2, flow_type=10), _app(3, flow_type=15)])
    result = _run(env, flow_types=[5, 10])
    assert [a['id'] for a in result['data']] == [1, 2]
    assert result['total'] == 2


def test_tag_ids_multi_or_semantics(monkeypatch):
    """AC-06: an app matching ANY selected tag stays in the list."""
    env = _Env(monkeypatch, [_app(1), _app(2), _app(3)])
    env.tag_resources = {1, 3}  # resources under the selected tags (OR union)
    result = _run(env, tag_ids=[10, 11])
    assert [a['id'] for a in result['data']] == [1, 3]


def test_sort_by_update_time(monkeypatch):
    """AC-07: update_time sorts newest first."""
    env = _Env(monkeypatch, [
        _app(1, update='2026-09-01T10:00:00'),
        _app(2, update='2026-09-02T10:00:00'),
    ])
    result = _run(env, sort_by='update_time')
    assert [a['id'] for a in result['data']] == [2, 1]


def test_sort_by_visit_count_zero_last(monkeypatch):
    """AC-20: visit_count desc; apps without visits rank last (AC-22)."""
    env = _Env(monkeypatch, [_app(1), _app(2), _app(3)])
    env.stats = {
        2: {'recent_7d_visits': 50, 'total_visits': 50, 'success_rate': 0.9, 'avg_process_time': 100.0},
        1: {'recent_7d_visits': 10, 'total_visits': 10, 'success_rate': 0.8, 'avg_process_time': 200.0},
    }
    result = _run(env, sort_by='visit_count')
    assert [a['id'] for a in result['data']] == [2, 1, 3]


def test_default_sort_keeps_service_order_and_paginates(monkeypatch):
    """AC-07/AC-08: default (recent) keeps get_all_flows order; pagination slices."""
    env = _Env(monkeypatch, [_app(i) for i in range(1, 6)])
    result = _run(env, page=2, limit=2)
    assert [a['id'] for a in result['data']] == [3, 4]
    assert result['total'] == 5


def test_stats_inline_for_page_items(monkeypatch):
    """AC-02: page items carry their stats dict."""
    env = _Env(monkeypatch, [_app(1), _app(2)])
    env.stats = {1: {'recent_7d_visits': 7, 'total_visits': 7, 'success_rate': 1.0, 'avg_process_time': 10.0}}
    result = _run(env, limit=10)
    assert result['data'][0]['stats']['recent_7d_visits'] == 7
    assert result['data'][1]['stats']['recent_7d_visits'] == 0
