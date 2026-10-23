"""Key 调用知识库的档位判定(2026-10-22)。

- 检索要求普通知识库的 `use_kb`(与工作站对话一致);
- 知识空间无 `use_*` 权限,不参与该判定;
- 既有 `assert_open_api_knowledge_visible*` 的实参错误一并回归。
"""

from types import SimpleNamespace

import pytest

from bisheng.common.errcode.open_api import OpenApiNotFoundError
from bisheng.knowledge.domain.models.knowledge import KnowledgeTypeEnum
from bisheng.open_api.domain.context import (
    OpenApiPrincipal,
    reset_open_api_principal,
    set_open_api_principal,
)
from bisheng.open_endpoints.domain import utils as ep_utils


def _principal() -> OpenApiPrincipal:
    return OpenApiPrincipal(
        credential_id=1,
        tenant_id=1,
        service_account_id=2,
        owner_user_id=3,
        key_mask="bs-sak-********AbCd",
        scopes=("knowledge:read",),
        owner_payload=SimpleNamespace(user_id=3),
        allowed_apps=None,
    )


@pytest.fixture
def key_principal():
    token = set_open_api_principal(_principal())
    try:
        yield
    finally:
        reset_open_api_principal(token)


def _patch_rows(monkeypatch, rows):
    from bisheng.knowledge.domain.models.knowledge import KnowledgeDao

    calls = []

    async def fake_rows(ids):
        calls.append(list(ids))
        return rows

    monkeypatch.setattr(KnowledgeDao, "aget_list_by_ids", fake_rows)
    return calls


def _patch_filter(monkeypatch, permitted_ids):
    from bisheng.knowledge.domain.services.knowledge_permission_service import KnowledgePermissionService

    calls = []

    async def fake_filter(self, login_user, knowledge_ids, permission_id):
        calls.append((tuple(knowledge_ids), permission_id))
        return [item for item in knowledge_ids if item in permitted_ids]

    monkeypatch.setattr(KnowledgePermissionService, "filter_knowledge_ids_by_permission_async", fake_filter)
    return calls


async def test_usable_helper_noop_without_principal(monkeypatch):
    calls = _patch_rows(monkeypatch, [])
    await ep_utils.assert_open_api_knowledge_usable([1, 2], SimpleNamespace(user_id=3))
    assert calls == []


async def test_usable_helper_skips_empty_ids(monkeypatch, key_principal):
    calls = _patch_rows(monkeypatch, [])
    await ep_utils.assert_open_api_knowledge_usable([], SimpleNamespace(user_id=3))
    assert calls == []


async def test_usable_helper_skips_knowledge_space(monkeypatch, key_principal):
    _patch_rows(monkeypatch, [SimpleNamespace(id=7, type=KnowledgeTypeEnum.SPACE.value)])
    filter_calls = _patch_filter(monkeypatch, permitted_ids=set())

    await ep_utils.assert_open_api_knowledge_usable([7], SimpleNamespace(user_id=3))

    assert filter_calls == []


async def test_usable_helper_allows_normal_kb_with_use_kb(monkeypatch, key_principal):
    _patch_rows(monkeypatch, [SimpleNamespace(id=7, type=KnowledgeTypeEnum.NORMAL.value)])
    filter_calls = _patch_filter(monkeypatch, permitted_ids={7})

    await ep_utils.assert_open_api_knowledge_usable([7], SimpleNamespace(user_id=3))

    assert filter_calls == [((7,), "use_kb")]


async def test_usable_helper_denies_normal_kb_without_use_kb(monkeypatch, key_principal):
    _patch_rows(monkeypatch, [SimpleNamespace(id=7, type=KnowledgeTypeEnum.NORMAL.value)])
    _patch_filter(monkeypatch, permitted_ids=set())

    with pytest.raises(OpenApiNotFoundError):
        await ep_utils.assert_open_api_knowledge_usable([7], SimpleNamespace(user_id=3))


def test_visible_sync_uses_permission_id_without_type_error(monkeypatch, key_principal):
    """P0 回归:原实现以类名调用实例方法,缺少 owner_user_id 会抛 TypeError/500。"""
    from bisheng.knowledge.domain.services.knowledge_permission_service import KnowledgePermissionService

    calls = []

    def fake_check(login_user, knowledge_id, permission_id):
        calls.append((knowledge_id, permission_id))
        return False

    monkeypatch.setattr(KnowledgePermissionService, "check_permission_id_sync", fake_check)

    with pytest.raises(OpenApiNotFoundError):
        ep_utils.assert_open_api_knowledge_visible_sync(7, SimpleNamespace(user_id=3))

    with pytest.raises(OpenApiNotFoundError):
        ep_utils.assert_open_api_knowledge_visible_sync(7, SimpleNamespace(user_id=3), write=True)

    assert calls == [(7, "view_kb"), (7, "edit_kb")]
