"""F076 T-12:/api/v2 的三道 fail-closed 守卫(design §2.5 / §5)。

1. **漏标守卫** —— 每条 `/api/v2` 路由的 endpoint 必须带 `@open_api_scope` 标记;
   漏标在运行期是 500/11811(服务端配置错误),这里把它变成 CI 红灯。
2. **注册表双向一致** —— scope 注册表声明的 `(METHOD, path)` 与真实路由集合
   必须完全相等(扣除 scope=None 的例外如 whoami):注册表多一条 = 幽灵端点,
   少一条 = 有端点没进权限位体系。
3. **默认强制守卫** —— 开关打开且默认租户已开启时,**任意**端点无凭据都必须
   401/11801(逐个路由驱动依赖,不允许存在裸奔端点)。

这三道是"新加路由默认放行"的唯一防线(F056 pitfall 的同类),方法学照
test/f040/test_static_scan.py 的路由矩阵范式,断言对象换成 FastAPI 路由表。
"""

import re

from fastapi import WebSocketException
from starlette.requests import Request

from bisheng.api.router import router_rpc
from bisheng.common.errcode.open_api import OpenApiKeyMissingError
from bisheng.core.config.open_api import OpenApiConf
from bisheng.core.context.tenant import DEFAULT_TENANT_ID
from bisheng.open_api.api import dependencies as deps
from bisheng.open_api.domain.scopes import (
    EXEMPT_ENDPOINTS,
    all_registered_endpoints,
    get_open_api_scope_marker,
)
from bisheng.open_api.domain.services import tenant_setting_service


def _routes():
    """(method, 归一化 path, endpoint) 三元组;WS 路由方法记作 'WS'。"""
    out = []
    for route in router_rpc.routes:
        path = re.sub(r"\{[^}]+\}", "{}", route.path)
        methods = getattr(route, "methods", None)
        if methods:
            for method in sorted(methods):
                if method in ("HEAD", "OPTIONS"):
                    continue
                out.append((method, path, route.endpoint))
        else:
            out.append(("WS", path, route.endpoint))
    return out


def test_every_v2_route_carries_a_scope_marker():
    """守卫 1:漏标即失败(运行期是 500/11811)。"""
    unmarked = sorted({f"{m} {p}" for m, p, e in _routes() if get_open_api_scope_marker(e) is None})
    assert unmarked == [], f"/api/v2 存在未标注权限位的端点: {unmarked}"


def test_registry_matches_real_routes_bidirectionally():
    """守卫 2:注册表 ↔ 真实路由 双向零差异(scope=None 的例外单独核对)。"""
    # 两侧都用同一归一化:参数名不参与比对({knowledge_id} 与 {id} 等价)。
    registered = {(m, re.sub(r"\{[^}]+\}", "{}", p)) for m, p in all_registered_endpoints()}
    real = {(m, p) for m, p, _ in _routes()}
    scopeless = {(m, p) for m, p, e in _routes() if get_open_api_scope_marker(e).scope is None}

    assert registered - real == set(), f"注册表存在幽灵端点: {registered - real}"
    assert real - registered == scopeless, (
        f"有端点既未登记进注册表、也不在无权限位例外中: {real - registered - scopeless}"
    )
    # 例外清单是显式枚举的:当前未落地的 whoami 允许提前登记,但落地后必须真的存在。
    assert EXEMPT_ENDPOINTS & real == EXEMPT_ENDPOINTS - {("GET", "/api/v2/auth/whoami")}


async def test_enforcement_on_rejects_keyless_calls_on_every_route(monkeypatch):
    """守卫 3:开关打开 + 默认租户开启时,任意端点无凭据一律 11801。"""
    monkeypatch.setattr(deps.settings, "open_api", OpenApiConf(enforce_keys=True))

    async def _enforced(tenant_id: int) -> bool:
        return tenant_id == DEFAULT_TENANT_ID

    monkeypatch.setattr(tenant_setting_service, "is_enforced", _enforced)

    # 免登录开关置"关":本守卫断言的是"除显式 guest 豁免外,任意端点无凭据必拦";
    # guest 端点自身的放行语义由 test_dependencies 的三条用例覆盖。
    async def _guest_off() -> bool:
        return False

    monkeypatch.setattr(deps, "_guest_access_enabled", _guest_off)

    leaks = []
    for method, path, endpoint in _routes():
        conn = _keyless_conn(method, path, endpoint)
        try:
            gen = deps.verify_open_api_access(conn)
            await gen.__anext__()  # 无凭据必须在这里抛 11801,而不是 yield
        except (OpenApiKeyMissingError, WebSocketException):
            # HTTP 走 11801,WS 换成 1008(design §4.3)——两者都算"已拦截"。
            continue
        leaks.append(f"{method} {path}")

    assert leaks == [], f"以下端点无凭据时未被拦截: {leaks}"


def _keyless_conn(method: str, path: str, endpoint):
    """无凭据的 HTTP / WS 连接桩(带 endpoint 供 marker 读取)。"""
    scope = {
        "type": "websocket" if method == "WS" else "http",
        "method": method,
        "path": path,
        "query_string": b"",
        "headers": [],
        "endpoint": endpoint,
    }
    if method != "WS":
        return Request(scope)

    from fastapi import WebSocket

    async def _receive():
        return {"type": "websocket.disconnect"}

    async def _send(message):
        return None

    return WebSocket(scope, receive=_receive, send=_send)


#: 平台免登录页依赖的 8 个端点(discovery 决策 D5)——冻结,新增端点默认不进豁免。
EXPECTED_GUEST_ENDPOINTS = {
    ("POST", "/api/v2/assistant/chat/completions"),
    ("GET", "/api/v2/assistant/info/{}"),
    ("GET", "/api/v2/assistant/list"),
    ("WS", "/api/v2/assistant/chat/{}"),
    ("WS", "/api/v2/workflow/chat/{}"),
    ("POST", "/api/v2/chat/gen_title"),
    ("GET", "/api/v2/chat/history"),
    ("GET", "/api/v2/flows/{}"),
}


def test_guest_endpoint_list_is_frozen():
    """T-14:guest 豁免面 = 恰好这 8 个;多一个少一个都要在这条断言前显式改测试。"""
    actual = {(m, p) for m, p, e in _routes() if get_open_api_scope_marker(e).guest}
    assert actual == EXPECTED_GUEST_ENDPOINTS
