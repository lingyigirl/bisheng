"""`POST /api/v2/filelib/query_qa` 的资源级校验(2026-10-22)。

该端点原先只校验 `knowledge:read` scope,不校验任何资源;Key 调用现在只返回
密钥身份有 `view_kb` 的知识库下的问答行。非 Key 调用保持旧行为。
"""

from types import SimpleNamespace

import pytest

from bisheng.open_api.domain.context import (
    OpenApiPrincipal,
    reset_open_api_principal,
    set_open_api_principal,
)
from bisheng.open_endpoints.api.endpoints import filelib
from bisheng.open_endpoints.domain.schemas.filelib import QueryQAParam


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


def _patch_rows(monkeypatch, knowledge_ids):
    from bisheng.knowledge.domain.models.knowledge_file import QAKnoweldgeDao

    rows = [SimpleNamespace(knowledge_id=kb_id, answers="[]") for kb_id in knowledge_ids]

    def fake_query(**kwargs):
        return rows

    monkeypatch.setattr(QAKnoweldgeDao, "query_by_condition_v1", fake_query)
    monkeypatch.setattr(filelib, "get_default_operator", lambda: SimpleNamespace(user_id=3))
    return rows


def _param():
    return QueryQAParam(timeRange=["2026-01-01 00:00:00", "2026-12-31 23:59:59"])


def test_query_qa_does_not_filter_without_principal(monkeypatch):
    from bisheng.knowledge.domain.services.knowledge_permission_service import KnowledgePermissionService

    _patch_rows(monkeypatch, [10, 20])

    def must_not_call(*args, **kwargs):
        raise AssertionError("permission check must not run for non-key calls")

    monkeypatch.setattr(KnowledgePermissionService, "check_permission_id_sync", must_not_call)

    result = filelib.query_qa(_param())

    assert [row.knowledge_id for row in result.data] == [10, 20]


def test_query_qa_filters_by_view_kb_for_keys(monkeypatch, key_principal):
    from bisheng.knowledge.domain.services.knowledge_permission_service import KnowledgePermissionService

    _patch_rows(monkeypatch, [10, 20, 20])
    calls = []

    def fake_check(login_user, knowledge_id, permission_id):
        calls.append((knowledge_id, permission_id))
        return knowledge_id == 10

    monkeypatch.setattr(KnowledgePermissionService, "check_permission_id_sync", fake_check)

    result = filelib.query_qa(_param())

    assert [row.knowledge_id for row in result.data] == [10]
    # 同一知识库只查一次(记忆化)。
    assert calls == [(10, "view_kb"), (20, "view_kb")]
