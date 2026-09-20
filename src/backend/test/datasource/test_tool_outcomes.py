"""F061 T-31: every query in a turn leaves a trace, not just the last one.

`last_outcome` used to be a single slot, so a model that queried twice in one
turn kept only the final result and the first anchor vanished -- the same class
of silent loss the citation exemption guards against.
"""

# ruff: noqa: RUF001 -- the Chinese test data mirrors user-facing text;
# fullwidth punctuation is correct in it.

from types import SimpleNamespace

from bisheng.datasource.domain.langchain.tool import DatasourceQueryTool
from bisheng.datasource.domain.services.executor import QueryOutcome


class _AllowedUser:
    """A caller that passes the per-call `viewer` check (T-32)."""

    user_id = 3
    tenant_id = 1

    def get_visible_tenants(self):
        return [1]

    def is_admin(self):
        return False


def _allow_queries(monkeypatch):
    from unittest.mock import AsyncMock

    from bisheng.permission.domain.services.permission_service import PermissionService

    monkeypatch.setattr(PermissionService, 'check', AsyncMock(return_value=True))


def _tool():
    return DatasourceQueryTool(
        name='datasource_7',
        description='在业务库中查询',
        datasource=SimpleNamespace(id=7, name='业务库', tenant_id=1, engine='mysql'),
        tables=[],
        user_id=3,
        login_user=_AllowedUser(),
    )


def _outcome(statement='SELECT 1'):
    return QueryOutcome(statement=statement, columns=['id'], rows=[[1]], row_count=1)


async def test_two_queries_both_leave_an_outcome(monkeypatch):
    _allow_queries(monkeypatch)
    tool = _tool()
    outcomes = [_outcome('SELECT 1'), _outcome('SELECT 2')]

    async def _answer_question(datasource, question, tables, **kwargs):
        from bisheng.datasource.domain.services.query_service import AnswerContext

        return AnswerContext(
            answer='ok',
            outcome=outcomes.pop(0),
            injection_mode='full',
            failure_category=None,
        )

    monkeypatch.setattr(
        'bisheng.datasource.domain.langchain.tool.DatasourceQueryService.answer_question',
        _answer_question,
    )

    await tool._arun('第一个问题')
    await tool._arun('第二个问题')

    statements = [o.statement for o in tool.outcomes]
    assert statements == ['SELECT 1', 'SELECT 2'], (
        'a second query overwrote the first -- the first anchor would be lost'
    )


async def test_a_failed_query_adds_no_outcome(monkeypatch):
    _allow_queries(monkeypatch)
    tool = _tool()

    async def _answer_question(datasource, question, tables, **kwargs):
        from bisheng.datasource.domain.services.query_service import AnswerContext

        return AnswerContext(
            answer='查询失败：表不存在',
            outcome=None,
            injection_mode='full',
            failure_category='execution',
        )

    monkeypatch.setattr(
        'bisheng.datasource.domain.langchain.tool.DatasourceQueryService.answer_question',
        _answer_question,
    )

    await tool._arun('失败的问题')

    assert tool.outcomes == []
