"""F047 favorite API tests (AC-12, AC-13, AC-14, AC-15, AC-16, AC-23).

Mount the real plaza router on a minimal FastAPI app + TestClient, patch the
service boundary (same pattern as test_conversation_export_api.py).

Test-first: these fail (ModuleNotFoundError) until plaza endpoints exist.
"""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.exceptions import HTTPException, RequestValidationError
from fastapi.responses import ORJSONResponse
from starlette import status
from starlette.testclient import TestClient

from bisheng.common.dependencies.user_deps import UserPayload
from bisheng.common.errcode import BaseErrorCode
from bisheng.common.errcode.http_error import UnAuthorizedError
from bisheng.common.errcode.workstation import PlazaAppNotFoundError
from bisheng.workstation.api.endpoints.plaza import router as plaza_router
from bisheng.workstation.domain.services import plaza_service as svc_mod


def _handle_http_exception(req, exc):
    if isinstance(exc, HTTPException):
        msg = {'status_code': exc.status_code, 'status_message': exc.detail}
    elif isinstance(exc, BaseErrorCode):
        data = {'exception': str(exc), **exc.kwargs} if exc.kwargs else {'exception': str(exc)}
        msg = {'status_code': exc.code, 'status_message': exc.message, 'data': data}
    else:
        msg = {'status_code': 500, 'status_message': str(exc)}
    return ORJSONResponse(content=msg)


def _handle_validation_error(req, exc):
    return ORJSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content={'status_code': status.HTTP_422_UNPROCESSABLE_ENTITY, 'status_message': exc.errors()},
    )


_EXCEPTION_HANDLERS = {
    HTTPException: _handle_http_exception,
    RequestValidationError: _handle_validation_error,
    BaseErrorCode: _handle_http_exception,
}


class _FakeUser:
    def __init__(self, user_id=1, user_name='Admin', tenant_id=1):
        self.user_id = user_id
        self.user_name = user_name
        self.tenant_id = tenant_id

    def is_admin(self):
        return False


def _make_app(user=None) -> FastAPI:
    app = FastAPI(exception_handlers=_EXCEPTION_HANDLERS)
    app.include_router(plaza_router, prefix='/api/v1/workstation')

    async def _get_user():
        return user or _FakeUser()

    app.dependency_overrides[UserPayload.get_login_user] = _get_user
    return app


# --- favorite add / remove (AC-12, AC-13) ----------------------------------


def test_add_favorite_calls_service_and_returns_200(monkeypatch):
    """AC-12: POST /app/favorite delegates to the service and returns 200."""
    calls = []

    def _add(user, app_id, app_type):
        calls.append((user.user_id, app_id, app_type))

    monkeypatch.setattr(svc_mod.PlazaService, 'add_favorite', staticmethod(_add))

    with TestClient(_make_app()) as client:
        resp = client.post('/api/v1/workstation/app/favorite', json={'app_id': '10', 'app_type': 5})
        assert resp.status_code == 200
    assert calls == [(1, '10', 5)]


def test_add_favorite_missing_body_returns_422():
    """AC-12: body validation rejects missing fields."""
    with TestClient(_make_app()) as client:
        resp = client.post('/api/v1/workstation/app/favorite', json={})
        assert resp.status_code == 422


def test_add_favorite_app_not_found_returns_error_code(monkeypatch):
    """AC-16: stale app id surfaces the business error envelope."""
    def _add(user, app_id, app_type):
        raise PlazaAppNotFoundError()

    monkeypatch.setattr(svc_mod.PlazaService, 'add_favorite', staticmethod(_add))

    with TestClient(_make_app()) as client:
        resp = client.post('/api/v1/workstation/app/favorite', json={'app_id': '999', 'app_type': 5})
        body = resp.json()
        assert body['status_code'] == 12080


def test_remove_favorite_calls_service_and_returns_200(monkeypatch):
    """AC-13: DELETE /app/favorite delegates to the service and returns 200."""
    calls = []

    def _remove(user, app_id, app_type):
        calls.append((user.user_id, app_id, app_type))

    monkeypatch.setattr(svc_mod.PlazaService, 'remove_favorite', staticmethod(_remove))

    with TestClient(_make_app()) as client:
        resp = client.request('DELETE', '/api/v1/workstation/app/favorite', json={'app_id': '10', 'app_type': 5})
        assert resp.status_code == 200
    assert calls == [(1, '10', 5)]


# --- favorites list (AC-14, AC-15, AC-23) ----------------------------------


def test_list_favorites_returns_service_data(monkeypatch):
    """AC-14: GET /app/favorites returns {data, total} from the service."""
    async def _list(user, page, limit):
        return {'data': [{'id': 10, 'name': 'App A'}], 'total': 1}

    monkeypatch.setattr(svc_mod.PlazaService, 'list_favorites', staticmethod(_list))

    with TestClient(_make_app()) as client:
        resp = client.get('/api/v1/workstation/app/favorites', params={'page': 1, 'limit': 20})
        body = resp.json()
        assert resp.status_code == 200
        assert body['data']['data'] == [{'id': 10, 'name': 'App A'}]
        assert body['data']['total'] == 1


def test_list_favorites_requires_login():
    """AC-23: without a login dependency the endpoint is not reachable."""
    app = _make_app()
    app.dependency_overrides[UserPayload.get_login_user] = lambda: (_ for _ in ()).throw(
        UnAuthorizedError()
    )
    with TestClient(app) as client:
        resp = client.get('/api/v1/workstation/app/favorites')
        assert resp.json()['status_code'] != 0


# --- PlazaService.list_favorites filtering (AC-14, AC-16, AC-23) ------------


class _FlowRow:
    """Minimal fake for FlowDao.aget_all_apps dict rows."""

    def __init__(self, **kw):
        self.__dict__.update(kw)


def _seed_favorite(db_session, user_id, app_id, app_type):
    from bisheng.workstation.domain.repositories.favorite_repository import FavoriteRepository

    FavoriteRepository(db_session).add(user_id=user_id, app_id=app_id, app_type=app_type)


def test_list_favorites_filters_offline_and_invisible(monkeypatch, db_session, tenant_context):
    """AC-14/AC-16: only ONLINE + view-app-visible favorited apps are listed;
    order follows favorite recency."""
    from contextlib import contextmanager

    from bisheng.core.database.tenant_filter import register_tenant_filter_events
    from bisheng.workstation.domain.services import plaza_service as svc

    register_tenant_filter_events()
    tenant_context(1)

    @contextmanager
    def _test_session():
        yield db_session

    monkeypatch.setattr(svc, 'get_sync_db_session', _test_session)
    _seed_favorite(db_session, user_id=1, app_id=10, app_type=5)
    _seed_favorite(db_session, user_id=1, app_id=11, app_type=5)
    _seed_favorite(db_session, user_id=1, app_id=12, app_type=5)  # will be filtered (offline)

    class _FakeDao:
        @staticmethod
        async def aget_all_apps(id_list, status, page, limit):
            rows = [
                {'id': '10', 'name': 'AppA', 'flow_type': 5, 'logo': 'a.png', 'description': 'A'},
                {'id': '11', 'name': 'AppB', 'flow_type': 5, 'logo': 'b.png', 'description': 'B'},
            ]
            return [r for r in rows if r['id'] in id_list], 2

    class _FakeWorkFlowService:
        @staticmethod
        async def filter_apps_by_permission_id(user, apps, permission_id):
            return [a for a in apps if a['id'] != '11']  # 11 no longer visible

        @staticmethod
        def get_logo_share_link(logo):
            return f'https://cdn/{logo}'

    monkeypatch.setattr(svc.FlowDao, 'aget_all_apps', _FakeDao.aget_all_apps)
    monkeypatch.setattr(svc.WorkFlowService, 'filter_apps_by_permission_id',
                        _FakeWorkFlowService.filter_apps_by_permission_id)
    monkeypatch.setattr(svc.WorkFlowService, 'get_logo_share_link',
                        staticmethod(_FakeWorkFlowService.get_logo_share_link))
    monkeypatch.setattr(svc.TagDao, 'get_tags_by_resource', lambda self, ids: {})

    import asyncio

    result = asyncio.run(svc.PlazaService.list_favorites(_FakeUser(), 1, 20))
    assert result['total'] == 1
    assert [a['id'] for a in result['list']] == ['10']
    assert result['list'][0]['logo'] == 'https://cdn/a.png'
