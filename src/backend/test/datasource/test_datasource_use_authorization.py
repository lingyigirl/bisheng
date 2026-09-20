"""F061 T-32: authorization is re-checked at USE time, not only at build time.

Spec AC-49's second half ("直接调用相关接口 SHALL 被拒绝") and AC-52 ("授权变更在此后的
对话轮次中按新集合生效") are both about the same thing: the check must happen when the
data is reached for, not once when the tool set is assembled.

That distinction is not academic here. `AssistantAgent` is REUSED across turns in
one chat -- `common/chat/client.py` only rebuilds it when the session switches --
so `init_tools` runs once per session. A check that lived only there would let a
revoked datasource keep answering questions in a chat that is already open, which
is exactly what AC-52 forbids and what the module docstring of
`conversation_service` claims does NOT happen (it says the check is per turn).

Two mechanisms are asserted separately because they fail differently:

  1. **The tool refuses at call time** when the invoking user has lost `viewer`.
     This covers an already-built tool, which is the case a per-session build
     cannot cover.
  2. **A tool built without an authorization context refuses everything.** This
     one is a tripwire rather than a feature: `build_datasource_tools` defaults
     `user_id` to None, so a future call site that forgets to pass a user would
     otherwise produce a permanently-authorized tool. Failing closed means the
     mistake surfaces as "the model cannot query" instead of "everyone can".

The permission check is asserted on, never mocked away -- it is the subject.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from bisheng.datasource.domain.langchain.tool import (
    DatasourceQueryTool,
    build_datasource_tools,
)
from bisheng.datasource.domain.services.executor import QueryOutcome
from bisheng.permission.domain.services.permission_service import PermissionService


def _datasource(ds_id=7, name='客户订单库'):
    return SimpleNamespace(id=ds_id, name=name, tenant_id=1, engine='mysql')


def _tool(**overrides):
    fields = {
        'name': 'datasource_7',
        'description': '在业务库中查询',
        'datasource': _datasource(),
        'tables': [],
        'user_id': 3,
    }
    fields.update(overrides)
    return DatasourceQueryTool(**fields)


def _outcome(statement='SELECT id FROM orders'):
    return QueryOutcome(statement=statement, columns=['id'], rows=[[1]], row_count=1)


class _User:
    """A caller that satisfies the `login_user` shape the check needs.

    `get_visible_tenants` must be callable: `PermissionService` skips its tenant
    gate for a `login_user` without one, which would silently drop tenant
    isolation rather than fail.
    """

    user_id = 3
    tenant_id = 1

    def get_visible_tenants(self):
        return [1]

    def is_admin(self):
        return False


def _allow(monkeypatch):
    checker = AsyncMock(return_value=True)
    monkeypatch.setattr(PermissionService, 'check', checker)
    return checker


def _deny(monkeypatch):
    checker = AsyncMock(return_value=False)
    monkeypatch.setattr(PermissionService, 'check', checker)
    return checker


# ── 1. the tool refuses at call time (AC-49 / AC-52) ──────────────────────────


async def test_a_tool_with_no_login_user_refuses_to_query(monkeypatch):
    """Fail closed. `user_id` alone is not an authorization context: the tenant
    gate in `PermissionService` needs the payload, so a tool carrying only an id
    cannot be checked and must not run."""
    answer = AsyncMock()
    monkeypatch.setattr(
        'bisheng.datasource.domain.services.query_service.DatasourceQueryService.answer_question',
        answer,
    )
    tool = _tool(login_user=None)

    result = await tool._arun('查一下订单')

    answer.assert_not_awaited()
    assert '查询失败' in result
    assert tool.outcomes == []


async def test_a_revoked_datasource_is_refused_on_the_next_call(monkeypatch):
    """AC-52's real case: the tool was built while the grant existed, the grant
    is then removed, and the very next call in the SAME session must refuse.

    Nothing is rebuilt between the two calls -- that is the point.
    """
    monkeypatch.setattr(
        'bisheng.datasource.domain.services.query_service.DatasourceQueryService.answer_question',
        AsyncMock(return_value=SimpleNamespace(outcome=_outcome(), failure_category=None, answer='ok')),
    )
    allowed = _allow(monkeypatch)
    tool = _tool(login_user=_User())

    await tool._arun('第一次提问')
    assert len(tool.outcomes) == 1, 'the granted call must have run'
    assert allowed.await_count == 1

    # The grant goes away. Same tool object, no rebuild.
    _deny(monkeypatch)
    result = await tool._arun('第二次提问')

    assert '查询失败' in result
    assert len(tool.outcomes) == 1, 'a refused call must leave no citation anchor'


async def test_the_refusal_does_not_leak_which_datasource_exists(monkeypatch):
    """The refusal is the same sentence as "not found" -- spec: telling them
    apart would let a caller probe which ids exist in another tenant."""
    _deny(monkeypatch)
    tool = _tool(login_user=_User())

    refused = await tool._arun('查一下订单')

    # The datasource's own name must not appear in what the model (and through
    # it, the user) receives.
    assert '客户订单库' not in refused


async def test_a_refused_call_is_not_filed_as_a_query_failure(monkeypatch):
    """The admin failure list exists to drive schema-description work (AC-42).
    An authorization refusal is not a schema problem, and no statement was ever
    sent to the database, so it must not appear there.

    Patched on `acreate_best_effort`, which is the name the failure trail
    actually calls -- a patched name that does not exist would make this test
    pass without asserting anything.
    """
    _deny(monkeypatch)
    failure_writer = AsyncMock()
    monkeypatch.setattr(
        'bisheng.datasource.domain.services.query_service.DbQueryFailureDao.acreate_best_effort',
        failure_writer,
    )
    tool = _tool(login_user=_User())

    await tool._arun('查一下订单')

    failure_writer.assert_not_awaited()


async def test_an_authorized_call_still_works(monkeypatch):
    """The guard must not break the normal path."""
    monkeypatch.setattr(
        'bisheng.datasource.domain.services.query_service.DatasourceQueryService.answer_question',
        AsyncMock(return_value=SimpleNamespace(outcome=_outcome(), failure_category=None, answer='ok')),
    )
    allowed = _allow(monkeypatch)
    tool = _tool(login_user=_User())

    result = await tool._arun('查一下订单')

    assert '查询失败' not in result
    assert len(tool.outcomes) == 1
    assert allowed.await_count == 1
    assert allowed.await_args.kwargs['relation'] == 'viewer'
    assert allowed.await_args.kwargs['object_type'] == 'db_datasource'
    assert allowed.await_args.kwargs['object_id'] == '7'


# ── 2. building without an authorization context fails closed ────────────────


async def test_build_requires_an_authorization_context():
    """A caller that forgets the credentials must not get a queryable tool.

    `build_datasource_tools` is called from `build_tools_for_assistant`, which
    already does the per-user filter -- but it is a public function with a
    `user_id=None` default, so the mistake is one keyword away.
    """
    with patch(
        'bisheng.datasource.domain.langchain.tool.DbDatasourceTableDao.alist_by_datasource',
        AsyncMock(return_value=[]),
    ):
        with pytest.raises(ValueError, match='login_user'):
            await build_datasource_tools([_datasource(7)])


async def test_build_keeps_the_context_on_the_tool_it_returns():
    """The built tool must carry what the per-call check needs; dropping it here
    would put every tool back into the fail-closed branch."""
    with patch(
        'bisheng.datasource.domain.langchain.tool.DbDatasourceTableDao.alist_by_datasource',
        AsyncMock(return_value=[]),
    ):
        tools = await build_datasource_tools([_datasource(7)], user_id=3, login_user=_User())

    assert tools[0].login_user is not None
    assert tools[0].user_id == 3


async def test_building_with_no_datasources_still_returns_empty_without_a_context():
    """The one legitimate context-free call: nothing to authorize. Existing
    assistants bind no datasource, so this is the common path and it must not
    start raising (AC-60: unchanged behavior when no datasource is configured)."""
    assert await build_datasource_tools([]) == []
