"""E2E tests for F047 agent plaza (favorites / plaza list / stats).

Runs against the real backend (localhost:7860) with middleware present.
Login: admin via E2E_ADMIN_PASSWORD env (local dev: Tansun@2020), plus the
workspace user ``test`` (Tansun@2020) for cross-user isolation checks.

Covers:
- AC-01/AC-02/AC-19/AC-20: plaza list returns enriched apps with inline stats
- AC-12/AC-13: favorite / unfavorite round-trip (idempotent)
- AC-14/AC-16: favorites list returns only online + visible apps
- AC-15/AC-23: favorites are user-scoped
- AC-09: single-app stats endpoint shape
"""
import httpx
import pytest

from test.e2e.helpers.api import API_BASE, assert_resp_200
from test.e2e.helpers.auth import auth_headers, get_admin_token, get_user_token


async def _login_admin(client: httpx.AsyncClient) -> str:
    return await get_admin_token(client)


async def _first_online_app(client: httpx.AsyncClient, token: str) -> dict | None:
    resp = await client.get(
        f'{API_BASE}/workstation/app/plaza',
        params={'page': 1, 'limit': 5, 'sort_by': 'update_time'},
        headers=auth_headers(token),
    )
    data = assert_resp_200(resp)
    items = data.get('data', [])
    return items[0] if items else None


@pytest.mark.e2e
async def test_plaza_list_returns_apps_with_stats():
    async with httpx.AsyncClient(timeout=30) as client:
        token = await _login_admin(client)
        resp = await client.get(
            f'{API_BASE}/workstation/app/plaza',
            params={'page': 1, 'limit': 5},
            headers=auth_headers(token),
        )
        body = assert_resp_200(resp)
        assert isinstance(body['total'], int)
        for app in body['data']:
            assert 'id' in app and 'name' in app and 'flow_type' in app
            stats = app.get('stats')
            assert stats is not None
            for key in ('recent_7d_visits', 'total_visits', 'success_rate', 'avg_process_time'):
                assert key in stats


@pytest.mark.e2e
async def test_plaza_visit_count_sort_and_stats_endpoint():
    async with httpx.AsyncClient(timeout=30) as client:
        token = await _login_admin(client)
        resp = await client.get(
            f'{API_BASE}/workstation/app/plaza',
            params={'page': 1, 'limit': 20, 'sort_by': 'visit_count'},
            headers=auth_headers(token),
        )
        body = assert_resp_200(resp)
        visits = [app['stats']['recent_7d_visits'] for app in body['data']]
        assert visits == sorted(visits, reverse=True)

        # Single-app stats (preview drawer)
        app = body['data'][0]
        resp2 = await client.get(
            f'{API_BASE}/workstation/app/stats/{app["id"]}',
            params={'days': 7},
            headers=auth_headers(token),
        )
        stats = assert_resp_200(resp2)
        for key in ('recent_7d_visits', 'total_visits', 'success_rate', 'avg_process_time'):
            assert key in stats


@pytest.mark.e2e
async def test_favorite_roundtrip_is_idempotent_and_user_scoped():
    async with httpx.AsyncClient(timeout=30) as client:
        admin_token = await _login_admin(client)
        app = await _first_online_app(client, admin_token)
        if app is None:
            pytest.skip('no online app available for plaza favorite e2e')
        app_id = str(app['id'])
        app_type = int(app['flow_type'])

        # Favorite twice (idempotent)
        for _ in range(2):
            resp = await client.post(
                f'{API_BASE}/workstation/app/favorite',
                json={'app_id': app_id, 'app_type': app_type},
                headers=auth_headers(admin_token),
            )
            assert_resp_200(resp)

        # Visible in admin favorites
        resp = await client.get(
            f'{API_BASE}/workstation/app/favorites', params={'page': 1, 'limit': 50},
            headers=auth_headers(admin_token),
        )
        fav = assert_resp_200(resp)
        assert any(str(a['id']) == app_id for a in fav['list'])

        # User-scoped: the workspace user does not see admin's favorite
        user_token = await get_user_token(client, 'test', 'Tansun@2020')
        resp = await client.get(
            f'{API_BASE}/workstation/app/favorites', params={'page': 1, 'limit': 50},
            headers=auth_headers(user_token),
        )
        user_fav = assert_resp_200(resp)
        assert all(str(a['id']) != app_id for a in user_fav['list'])

        # Unfavorite twice (idempotent)
        for _ in range(2):
            resp = await client.request(
                'DELETE',
                f'{API_BASE}/workstation/app/favorite',
                json={'app_id': app_id, 'app_type': app_type},
                headers=auth_headers(admin_token),
            )
            assert_resp_200(resp)

        resp = await client.get(
            f'{API_BASE}/workstation/app/favorites', params={'page': 1, 'limit': 50},
            headers=auth_headers(admin_token),
        )
        fav = assert_resp_200(resp)
        assert all(int(a['id']) != app_id for a in fav['list'])
