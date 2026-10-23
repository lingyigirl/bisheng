"""F076 increment: per-key agent allowlist primitives (spec AC-42/AC-45/AC-46).

Pure-function tests: normalization, NULL vs [] semantics, fail-closed parsing
and the 404 helper. Route-level enforcement is covered separately.
"""

import uuid

import pytest

from bisheng.common.errcode.open_api import OpenApiNotFoundError
from bisheng.open_api.domain.agent_scope import (
    agent_allowed,
    allowed_apps_to_json,
    assert_open_api_agent_allowed,
    assert_open_api_agent_allowed_any,
    assistant_ids_in_allowlist,
    normalize_agent_id,
    parse_allowed_apps,
)
from bisheng.open_api.domain.context import (
    OpenApiPrincipal,
    reset_open_api_principal,
    set_open_api_principal,
)

ASSISTANT_ID = uuid.uuid4().hex
WORKFLOW_ID = uuid.uuid4().hex


def _principal(allowed_apps):
    return OpenApiPrincipal(
        credential_id=1,
        tenant_id=1,
        service_account_id=2,
        owner_user_id=3,
        key_mask="bs-sak-********AbCd",
        scopes=("assistant:invoke",),
        owner_payload=object(),
        allowed_apps=allowed_apps,
    )


def test_normalize_agent_id_accepts_dashed_and_plain_uuid():
    dashed = "12345678-1234-5678-1234-567812345678"
    plain = "12345678123456781234567812345678"
    assert normalize_agent_id("assistant", dashed) == plain
    assert normalize_agent_id("workflow", plain) == plain
    assert normalize_agent_id("managed_agent", "7") == "7"
    assert normalize_agent_id("managed_agent", 7) == "7"
    assert normalize_agent_id("managed_agent", "0") is None
    assert normalize_agent_id("assistant", "not-a-uuid") is None
    assert normalize_agent_id("unknown", plain) is None


def test_parse_allowed_apps_keeps_null_and_empty_distinct():
    assert parse_allowed_apps(None) is None
    assert parse_allowed_apps([]) == frozenset()
    parsed = parse_allowed_apps([{"type": "assistant", "id": ASSISTANT_ID}])
    assert parsed == frozenset({("assistant", ASSISTANT_ID)})
    # Malformed non-null payloads fail closed: deny every agent.
    assert parse_allowed_apps("nope") == frozenset()
    assert parse_allowed_apps([{"type": "bogus", "id": ASSISTANT_ID}]) == frozenset()


def test_agent_allowed_honours_null_and_empty():
    assert agent_allowed(_principal(None), "assistant", ASSISTANT_ID) is True
    assert agent_allowed(_principal(frozenset()), "assistant", ASSISTANT_ID) is False
    allowed = frozenset({("assistant", ASSISTANT_ID)})
    assert agent_allowed(_principal(allowed), "assistant", ASSISTANT_ID) is True
    assert agent_allowed(_principal(allowed), "workflow", WORKFLOW_ID) is False


def test_assert_helpers_raise_11809_for_unlisted_agent():
    token = set_open_api_principal(_principal(frozenset({("assistant", ASSISTANT_ID)})))
    try:
        assert_open_api_agent_allowed("assistant", ASSISTANT_ID)
        with pytest.raises(OpenApiNotFoundError):
            assert_open_api_agent_allowed("workflow", WORKFLOW_ID)
        assert_open_api_agent_allowed_any(ASSISTANT_ID)
        with pytest.raises(OpenApiNotFoundError):
            assert_open_api_agent_allowed_any(WORKFLOW_ID)
    finally:
        reset_open_api_principal(token)


def test_empty_allowlist_denies_everything_and_lists_no_assistants():
    token = set_open_api_principal(_principal(frozenset()))
    try:
        with pytest.raises(OpenApiNotFoundError):
            assert_open_api_agent_allowed_any(ASSISTANT_ID)
        assert assistant_ids_in_allowlist() == set()
    finally:
        reset_open_api_principal(token)


def test_assistant_ids_and_whoami_shape():
    allowed = frozenset({("assistant", ASSISTANT_ID), ("workflow", WORKFLOW_ID)})
    token = set_open_api_principal(_principal(allowed))
    try:
        assert assistant_ids_in_allowlist() == {ASSISTANT_ID}
        assert allowed_apps_to_json(_principal(allowed)) == [
            {"type": "assistant", "id": ASSISTANT_ID},
            {"type": "workflow", "id": WORKFLOW_ID},
        ]
        assert allowed_apps_to_json(_principal(None)) is None
    finally:
        reset_open_api_principal(token)


async def test_app_visible_checks_the_allowlist_before_fga(monkeypatch):
    """未命中白名单时在 FGA 之前就返回 11809;命中后才走原有可见性判定。"""
    from bisheng.open_api.domain.context import set_open_api_principal as _set
    from bisheng.open_endpoints.domain.utils import assert_open_api_app_visible
    from bisheng.permission.domain.services.application_permission_service import (
        ApplicationPermissionService,
    )

    calls: list[tuple] = []

    async def _has(user, object_type, object_id, permission_ids):
        calls.append((object_type, object_id))
        return True

    monkeypatch.setattr(ApplicationPermissionService, "has_any_permission_async", _has)
    token = _set(_principal(frozenset({("workflow", WORKFLOW_ID)})))
    try:
        await assert_open_api_app_visible(WORKFLOW_ID, object())
        assert calls == [("workflow", WORKFLOW_ID)]

        with pytest.raises(OpenApiNotFoundError):
            await assert_open_api_app_visible("b" * 32, object())
        # Unlisted id must not reach the permission engine.
        assert calls == [("workflow", WORKFLOW_ID)]
    finally:
        reset_open_api_principal(token)


async def test_load_names_accepts_list_grants_row_shape(monkeypatch):
    """回归(2026-10-22): list_grants 返回 resource_type/resource_id,不是 type/id。

    之前的实现读 row["type"],账号一旦有智能体授权就让 assignable-agents
    直接 500(前端表现为「服务器错误」+ 下拉为空)。
    """
    from types import SimpleNamespace

    from bisheng.open_api.domain.services import agent_scope_service as svc

    assistant_id = "a" * 32

    async def _assistants(ids):
        assert ids == [assistant_id]
        return [SimpleNamespace(id=assistant_id, name="助手 A")]

    monkeypatch.setattr(svc.AssistantDao, "aget_assistants_by_ids", _assistants)

    names = await svc._load_names(
        [{"resource_type": "assistant", "resource_id": assistant_id, "level": "viewer", "source": "direct"}]
    )

    assert names == {("assistant", assistant_id): "助手 A"}


async def test_list_assignable_agents_uses_grant_row_fields(monkeypatch):
    """回归(2026-10-22): 走完整 list_assignable_agents,不 mock _load_names。"""
    from types import SimpleNamespace

    from bisheng.open_api.domain.services import agent_scope_service as svc
    from bisheng.permission.domain.services.application_permission_service import (
        ApplicationPermissionService,
    )

    assistant_id = "b" * 32

    async def _grants(_owner_user_id):
        return [
            {
                "resource_type": "assistant",
                "resource_id": assistant_id,
                "level": "viewer",
                "source": "direct",
            }
        ]

    async def _assistants(ids):
        assert ids == [assistant_id]
        return [SimpleNamespace(id=assistant_id, name="助手 B")]

    async def _has(user, object_type, object_id, permission_ids):
        return True

    monkeypatch.setattr(svc, "list_grants", _grants)
    monkeypatch.setattr(svc.AssistantDao, "aget_assistants_by_ids", _assistants)
    monkeypatch.setattr(ApplicationPermissionService, "has_any_permission_async", _has)

    rows = await svc.list_assignable_agents(SimpleNamespace(user_id=99))

    assert rows == [
        {
            "type": "assistant",
            "id": assistant_id,
            "name": "助手 B",
            "level": "viewer",
            "invocable": True,
        }
    ]
