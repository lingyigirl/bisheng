"""F047 single-app stats API tests (AC-09, AC-19)."""
from __future__ import annotations

from fastapi import FastAPI
from fastapi.exceptions import HTTPException, RequestValidationError
from fastapi.responses import ORJSONResponse
from starlette.testclient import TestClient

from bisheng.common.dependencies.user_deps import UserPayload
from bisheng.common.errcode import BaseErrorCode
from bisheng.common.errcode.workstation import PlazaAppNotFoundError, PlazaAppNotVisibleError
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
        status_code=422,
        content={'status_code': 422, 'status_message': exc.errors()},
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


def _make_app():
    app = FastAPI(exception_handlers=_EXCEPTION_HANDLERS)
    app.include_router(plaza_router, prefix='/api/v1/workstation')

    async def _get_user():
        return _FakeUser()

    app.dependency_overrides[UserPayload.get_login_user] = _get_user
    return app


def test_stats_returns_service_payload(monkeypatch):
    """AC-09/AC-19: GET /app/stats/{id} delegates and returns the stats dict."""
    async def _stats(user, app_id, days=7):
        return {'recent_7d_visits': 7, 'total_visits': 100, 'success_rate': 0.9, 'avg_process_time': 500.0}

    monkeypatch.setattr(svc_mod.PlazaService, 'app_stats', staticmethod(_stats))

    with TestClient(_make_app()) as client:
        resp = client.get('/api/v1/workstation/app/stats/10', params={'days': 7})
        assert resp.status_code == 200
        assert resp.json()['data']['recent_7d_visits'] == 7


def test_stats_days_param_passed_through(monkeypatch):
    """AC-19: the days window is forwarded to the service."""
    calls = []

    async def _stats(user, app_id, days=7):
        calls.append(days)
        return {}

    monkeypatch.setattr(svc_mod.PlazaService, 'app_stats', staticmethod(_stats))

    with TestClient(_make_app()) as client:
        client.get('/api/v1/workstation/app/stats/10', params={'days': 30})
    assert calls == [30]


def test_stats_app_not_found_returns_12080(monkeypatch):
    """AC-16: missing app surfaces the business error envelope."""
    async def _stats(user, app_id, days=7):
        raise PlazaAppNotFoundError()

    monkeypatch.setattr(svc_mod.PlazaService, 'app_stats', staticmethod(_stats))

    with TestClient(_make_app()) as client:
        resp = client.get('/api/v1/workstation/app/stats/999')
        assert resp.json()['status_code'] == 12080


def test_stats_app_not_visible_returns_12081(monkeypatch):
    """AC-16: offline / invisible app surfaces the business error envelope."""
    async def _stats(user, app_id, days=7):
        raise PlazaAppNotVisibleError()

    monkeypatch.setattr(svc_mod.PlazaService, 'app_stats', staticmethod(_stats))

    with TestClient(_make_app()) as client:
        resp = client.get('/api/v1/workstation/app/stats/10')
        assert resp.json()['status_code'] == 12081
