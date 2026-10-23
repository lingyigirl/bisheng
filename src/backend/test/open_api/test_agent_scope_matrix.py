"""F076 increment: static guard for the per-key agent allowlist (spec AC-46).

Every ``/api/v2`` route whose scope addresses an agent object must run the
allowlist check. The route list is derived from the **scope registry**, not a
frozen snapshot, so a newly added app-scope route makes this test fail until it
is either guarded, explicitly filter-only, or explicitly exempted:

- ``GUARDED``: handler source must call ``assert_open_api_agent_allowed*`` or
  ``assert_open_api_app_visible*`` (the latter embeds the allowlist check).
- ``FILTER_ONLY``: ``/assistant/list`` filters by ``assistant_ids_in_allowlist``.

ASR/TTS live under the separate ``llm:workbench`` scope and address no agent, so
they are outside the app-scope route set entirely (2026-10-22 security split).
"""

import inspect
import re

import pytest

from bisheng.api.router import router_rpc
from bisheng.open_api.domain.scopes import OPEN_API_SCOPES, WS

_APP_SCOPES = frozenset(
    {
        "assistant:invoke",
        "assistant:read",
        "workflow:invoke",
        "workflow:read",
        "chat:invoke",
        "managed_agent:invoke",
        "schedule:trigger",
    }
)

GUARDED = frozenset(
    {
        ("POST", "/api/v2/assistant/chat/completions"),
        ("GET", "/api/v2/assistant/info/{}"),
        (WS, "/api/v2/assistant/chat/{}"),
        ("POST", "/api/v2/workflow/invoke"),
        ("POST", "/api/v2/workflow/stop"),
        (WS, "/api/v2/workflow/chat/{}"),
        ("GET", "/api/v2/flows/{}"),
        ("POST", "/api/v2/chat/gen_title"),
        ("GET", "/api/v2/chat/history"),
        ("POST", "/api/v2/chat/liked"),
        ("POST", "/api/v2/chat/solved"),
        ("POST", "/api/v2/chat/comment"),
        ("POST", "/api/v2/chat/sync/messages"),
        ("POST", "/api/v2/managed-agent/invoke"),
        ("POST", "/api/v2/schedule/trigger"),
    }
)
FILTER_ONLY = frozenset({("GET", "/api/v2/assistant/list")})

_HELPER_NAMES = (
    "assert_open_api_agent_allowed",
    "assert_open_api_agent_allowed_any",
    "assert_open_api_app_visible",
)


def _normalize(path: str) -> str:
    return re.sub(r"\{[^}]+\}", "{}", path)


def _route_index() -> dict[tuple[str, str], object]:
    """(method, normalized path) -> endpoint, mirroring test_route_matrix."""
    out: dict[tuple[str, str], object] = {}
    for route in router_rpc.routes:
        path = _normalize(route.path)
        methods = getattr(route, "methods", None)
        if methods:
            for method in sorted(methods):
                if method in ("HEAD", "OPTIONS"):
                    continue
                out[(method, path)] = route.endpoint
        else:
            out[(WS, path)] = route.endpoint
    return out


def _app_scope_routes() -> set[tuple[str, str]]:
    return {
        (method, _normalize(path))
        for scope in OPEN_API_SCOPES
        if scope.code in _APP_SCOPES
        for method, path in scope.endpoints
    }


def test_every_agent_addressed_route_is_classified():
    routes = _route_index()
    actual = _app_scope_routes() & set(routes)
    expected = set(GUARDED) | set(FILTER_ONLY)

    # The managed-agent face is mounted only when its feature flag is on.
    managed_pair = ("POST", "/api/v2/managed-agent/invoke")
    if managed_pair not in routes:
        expected.discard(managed_pair)
        assert managed_pair not in actual

    assert actual - expected == set(), f"未分类的智能体寻址路由: {sorted(actual - expected)}"
    assert expected - actual == set(), f"清单中存在未挂载的路由: {sorted(expected - actual)}"


@pytest.mark.parametrize("route", sorted(GUARDED))
def test_guarded_route_calls_a_helper(route):
    routes = _route_index()
    if route not in routes:
        pytest.skip("route is not mounted in this configuration")
    source = inspect.getsource(routes[route])
    assert any(name in source for name in _HELPER_NAMES), (
        f"{route[0]} {route[1]} 没有调用智能体白名单 helper;新增端点必须显式接入(AC-46)"
    )


def test_assistant_list_filters_by_allowlist():
    routes = _route_index()
    route = ("GET", "/api/v2/assistant/list")
    source = inspect.getsource(routes[route])
    assert "assistant_ids_in_allowlist" in source, "assistant/list 必须按每 Key 白名单过滤(AC-45)"
