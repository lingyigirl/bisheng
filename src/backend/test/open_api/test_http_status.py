"""F076 T-13:开放 API 失败对外的**真实 HTTP 状态** + 统一信封(design §3 决策 3)。

两条证据链:
1. 处理器单元级 —— 118xx 映射到 HttpStatus,信封形状与平台一致;
2. 真实应用级(TestClient,真实路由 + 真实异常处理器)——开关打开后对 /api/v2
   的无凭据调用返回 HTTP 401 而非平台惯例的 "HTTP 200 + 信封"(AC-26)。
   这一步不需要 DB:依赖在凭据解析阶段就拒绝了。
"""

import json

import pytest
from starlette.requests import Request

from bisheng.common.errcode.open_api import (
    OpenApiAccountDisabledError,
    OpenApiKeyInvalidError,
    OpenApiKeyMissingError,
    OpenApiNotFoundError,
    OpenApiOwnerPrivilegedError,
    OpenApiScopeMissingError,
    OpenApiToolNotAllowedError,
)
from bisheng.core.config.open_api import OpenApiConf
from bisheng.core.context.tenant import DEFAULT_TENANT_ID
from bisheng.main import handle_open_api_error
from bisheng.open_api.api import dependencies as deps
from bisheng.open_api.domain.services import tenant_setting_service


@pytest.mark.parametrize(
    ("exc", "http_status"),
    [
        (OpenApiKeyMissingError(), 401),
        (OpenApiKeyInvalidError(), 401),
        (OpenApiAccountDisabledError(), 401),
        (OpenApiScopeMissingError(), 403),
        (OpenApiToolNotAllowedError(), 403),
        (OpenApiOwnerPrivilegedError(), 403),
        (OpenApiNotFoundError(), 404),
    ],
)
async def test_handler_maps_codes_to_real_status(exc, http_status):
    response = await handle_open_api_error(_dummy_request(), exc)
    assert response.status_code == http_status
    body = json.loads(response.body)
    assert body["status_code"] == exc.Code
    assert body["status_message"] == exc.Msg


async def test_handler_never_touches_the_request():
    """WS scope 下没有 method 属性 —— 处理器必须**完全不碰** request(design §5 坑 3)。

    用一个"碰任何属性就炸"的毒化桩:一旦 handler 将来加了 req.method / req.url
    之类的访问,本用例立刻变红 —— 而不是等某次 WS 非法密钥握手时冒出 500。
    """

    class _PoisonRequest:
        def __getattr__(self, name):
            raise AssertionError(f"open-api error handler touched request.{name}")

    response = await handle_open_api_error(_PoisonRequest(), OpenApiKeyMissingError())
    assert response.status_code == 401


def _dummy_request():
    scope = {"type": "http", "method": "GET", "path": "/api/v2/x", "headers": [], "query_string": b""}
    return Request(scope)


# ---------------------------------------------------------------------------
# 真实应用级:TestClient + 真实异常处理器
# ---------------------------------------------------------------------------


def test_enforced_keyless_call_returns_real_401(test_client, monkeypatch):
    monkeypatch.setattr(deps.settings, "open_api", OpenApiConf(enforce_keys=True))

    async def _enforced(tenant_id: int) -> bool:
        return tenant_id == DEFAULT_TENANT_ID

    monkeypatch.setattr(tenant_setting_service, "is_enforced", _enforced)

    async def _guest_off() -> bool:
        return False

    monkeypatch.setattr(deps, "_guest_access_enabled", _guest_off)

    response = test_client.get("/api/v2/assistant/list")

    assert response.status_code == 401, "无凭据必须回真实 HTTP 401,而不是 HTTP 200 + 信封"
    body = response.json()
    assert body["status_code"] == 11801
