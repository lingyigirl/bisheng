"""F076 T-08:scope 注册表与 marker 助手的单元测试。

覆盖 design §2.5(逐条硬编码、不用通配)、§2.6(8 项权限位、隐含规则)。
注册表与真实路由的**双向一致**由 T-12 的守卫测试负责;本文件只锁住注册表
自身的形状,避免它在被守卫发现之前先悄悄漂移。
"""

import pytest

from bisheng.open_api.domain.scopes import (
    EXEMPT_ENDPOINTS,
    OPEN_API_SCOPE_CODES,
    OPEN_API_SCOPES,
    WS,
    all_registered_endpoints,
    get_open_api_scope_marker,
    is_scope_granted,
    open_api_scope,
)


def test_registry_covers_44_routes():
    """/api/v2 实测 44 条路由 = 42 HTTP + 2 WS(design §4.2 的端点全集)。"""
    pairs = all_registered_endpoints()
    assert len(pairs) == 44
    assert len([p for p in pairs if p[0] == WS]) == 2
    assert len(pairs) - len([p for p in pairs if p[0] == WS]) == 42


def test_registry_has_no_duplicate_endpoint():
    """同一端点不得挂在两个权限位下(否则判定顺序会变得隐晦)。"""
    seen: set[tuple[str, str]] = set()
    for scope in OPEN_API_SCOPES:
        for pair in scope.endpoints:
            assert pair not in seen, f"{pair} 被重复登记"
            seen.add(pair)


def test_registry_shape():
    """8 项权限位;delegate 为二期预留(当前无端点)。"""
    assert len(OPEN_API_SCOPES) == 8
    assert OPEN_API_SCOPE_CODES == {
        "workflow:invoke",
        "workflow:read",
        "assistant:invoke",
        "assistant:read",
        "chat:invoke",
        "knowledge:read",
        "knowledge:write",
        "delegate",
    }
    delegate = next(s for s in OPEN_API_SCOPES if s.code == "delegate")
    assert delegate.endpoints == ()


def test_exempt_endpoints_are_not_in_registry():
    """whoami 是不需要权限位的例外,且不得同时出现在注册表里。"""
    assert EXEMPT_ENDPOINTS == {("GET", "/api/v2/auth/whoami")}
    assert not (all_registered_endpoints() & set(EXEMPT_ENDPOINTS))


def test_decorator_sets_marker_and_rejects_unknown_scope():
    @open_api_scope("workflow:invoke")
    def _endpoint():
        return None

    marker = get_open_api_scope_marker(_endpoint)
    assert marker is not None
    assert marker.scope == "workflow:invoke"

    # scope=None 是合法写法(需凭据、不需权限位)
    @open_api_scope(None)
    def _whoami():
        return None

    assert get_open_api_scope_marker(_whoami).scope is None

    with pytest.raises(ValueError):
        open_api_scope("not:a:scope")


def test_marker_missing_means_unmarked():
    def _bare():
        return None

    assert get_open_api_scope_marker(_bare) is None


def test_implied_scope_rule():
    """唯一隐含规则:knowledge:write 蕴含 knowledge:read(design §2.6)。"""
    assert is_scope_granted("knowledge:read", {"knowledge:write"}) is True
    assert is_scope_granted("knowledge:write", {"knowledge:read"}) is False
    assert is_scope_granted("workflow:invoke", {"workflow:invoke"}) is True
    assert is_scope_granted("workflow:invoke", {"assistant:invoke"}) is False
    # required=None:仅需凭据
    assert is_scope_granted(None, set()) is True
