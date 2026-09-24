"""F061 follow-up: who still references a datasource, and the delete that refuses.

Deleting a datasource does not fail loudly today. The Agent node's
`_init_datasource_tool` returns no tool when its datasource is gone, so a
workflow quietly stops answering from the database; an assistant drops the tool
on its next session. Both look like the model choosing not to query.

So the delete consults a reference list first and refuses while it is non-empty,
and the page asks for the same list so it can NAME what is in the way.

Two storage shapes, and the tests below cover both because only one of them is
queryable: assistants are a real column (`assistantlink.datasource_id`), while
workflows keep the id inside `flow.data`, a JSON blob with no column and no
foreign key. A test that mocked `alist_references` would cover neither.

The fake session is the one used by `test/datasource/test_dao.py`: the
statements are built against the real models and asserted as SQL, so the queries
under test are the queries that would run.
"""

from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy.dialects import mysql

from bisheng.common.errcode.datasource import DatasourceInUseError, DatasourceNotFoundError
from bisheng.datasource.api.endpoints.datasources import (
    delete_datasource,
    list_datasource_references,
)
from bisheng.datasource.domain.services import references as refs
from bisheng.permission.domain.services.permission_service import PermissionService

DS_ID = 7


def _sql_agent_node(datasource_id):
    return {
        'data': {
            'group_params': [
                {'params': [{'key': 'model_id', 'value': 3}]},
                {'params': [{'key': 'sql_agent', 'value': {'open': True, 'datasource_id': datasource_id}}]},
            ]
        }
    }


# ---------------------------------------------------------------------------
# The workflow half: the id lives inside `flow.data`
# ---------------------------------------------------------------------------


def test_a_flow_binding_the_datasource_is_recognised():
    assert refs._flow_binds_datasource({'nodes': [_sql_agent_node(DS_ID)]}, DS_ID) is True


def test_the_id_is_matched_as_a_string_so_both_json_shapes_work():
    """The editor has written this id as a number and as a string across its
    life; a numeric-only comparison would report "not referenced" for the other
    and let the delete through."""
    assert refs._flow_binds_datasource({'nodes': [_sql_agent_node(str(DS_ID))]}, DS_ID) is True


def test_another_datasource_does_not_match():
    assert refs._flow_binds_datasource({'nodes': [_sql_agent_node(DS_ID + 1)]}, DS_ID) is False


def test_a_datasource_id_on_a_DIFFERENT_param_does_not_match():
    """The key is what identifies the Agent node's SQL block. Matching any
    `datasource_id` anywhere would let an unrelated node block a delete."""
    node = {'data': {'group_params': [{'params': [{'key': 'some_other_node', 'value': {'datasource_id': DS_ID}}]}]}}
    assert refs._flow_binds_datasource({'nodes': [node]}, DS_ID) is False


@pytest.mark.parametrize('data', [None, {}, {'nodes': []}, {'nodes': None}, 'not-a-dict'])
def test_malformed_flow_data_does_not_raise(data):
    """`data` is a free-form JSON column; a flow saved half-finished must not
    turn a delete into a 500."""
    assert refs._flow_binds_datasource(data, DS_ID) is False


# ---------------------------------------------------------------------------
# Listing
# ---------------------------------------------------------------------------


class _Result:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


class _Session:
    def __init__(self, results):
        self._results = list(results)
        self.statements = []

    async def exec(self, stmt):
        self.statements.append(stmt)
        return _Result(self._results.pop(0))


def _rendered(stmt) -> str:
    """The statement as SQL with bound values inline.

    Assertions below pin the PREDICATE, not merely the column name. Checking for
    `assistant.is_delete` alone passes for `is_delete >= 0` too — a mutation
    that dropped the "skip deleted assistants" filter survived the whole file
    until this helper replaced the substring checks.
    """
    return str(stmt.compile(dialect=mysql.dialect(), compile_kwargs={'literal_binds': True}))


def _compiled(stmt):
    """Compiled SQL plus its bound parameters, for predicates whose type has no
    literal renderer (a `LIKE` against TEXT)."""
    compiled = stmt.compile(dialect=mysql.dialect())
    return str(compiled), compiled.params


def _patch_sessions(monkeypatch, results):
    session = _Session(results)

    @asynccontextmanager
    async def _scope():
        yield session

    monkeypatch.setattr(refs, 'get_async_db_session', _scope)
    return session


async def test_only_flows_that_actually_bind_it_are_reported(monkeypatch):
    """The LIKE is a prefilter, not the answer: a flow can mention the token and
    still bind a different datasource."""
    session = _patch_sessions(
        monkeypatch,
        [
            [],  # assistants
            [
                ('flow-1', 'risk-report', {'nodes': [_sql_agent_node(DS_ID)]}),
                ('flow-2', 'hr-report', {'nodes': [_sql_agent_node(DS_ID + 1)]}),
                ('flow-3', 'no-sql-block', {'nodes': [{'data': {}}]}),
            ],
        ],
    )

    found = await refs.alist_references(DS_ID)

    assert [one['name'] for one in found['flows']] == ['risk-report']
    assert found['assistants'] == []
    # The prefilter is on the raw column, which is what keeps this dual-DB: a
    # JSON-path function here would have no DM8 equivalent.
    # Pinned through the bound parameter: `literal_binds` has no renderer for a
    # LIKE against a TEXT column, and a bare column-name check would pass for
    # any predicate at all.
    flow_sql, flow_params = _compiled(session.statements[1])
    assert 'flow.data LIKE' in flow_sql
    assert '%datasource_id%' in flow_params.values()


async def test_the_assistant_query_narrows_on_the_column_and_skips_deleted(monkeypatch):
    """A deleted assistant's binding row survives; reporting it would block a
    delete on account of something the admin already threw away."""
    session = _patch_sessions(monkeypatch, [[('a-1', 'assistant-one')], []])

    found = await refs.alist_references(DS_ID)

    assert found['assistants'] == [{'id': 'a-1', 'name': 'assistant-one'}]
    assistant_sql = _rendered(session.statements[0])
    assert f'assistantlink.datasource_id = {DS_ID}' in assistant_sql
    # `>= 0` mentions the column too; only `= 0` actually skips deleted ones.
    assert 'assistant.is_delete = 0' in assistant_sql


async def test_names_are_sorted_so_the_page_can_render_them_as_is(monkeypatch):
    _patch_sessions(
        monkeypatch,
        [
            [('b', 'zebra'), ('a', 'alpha')],
            [('f2', 'zulu', {'nodes': [_sql_agent_node(DS_ID)]}), ('f1', 'bravo', {'nodes': [_sql_agent_node(DS_ID)]})],
        ],
    )

    found = await refs.alist_references(DS_ID)

    assert [one['name'] for one in found['assistants']] == ['alpha', 'zebra']
    assert [one['name'] for one in found['flows']] == ['bravo', 'zulu']


# ---------------------------------------------------------------------------
# The delete guard
# ---------------------------------------------------------------------------


class _User:
    user_id = 3
    tenant_id = 1

    def get_visible_tenants(self):
        return [1]


async def _delete(monkeypatch, references):
    monkeypatch.setattr(PermissionService, 'check', AsyncMock(return_value=True))
    monkeypatch.setattr(refs, 'alist_references', AsyncMock(return_value=references))
    monkeypatch.setattr(
        'bisheng.datasource.api.endpoints.datasources.alist_references',
        AsyncMock(return_value=references),
    )
    existing = SimpleNamespace(id=DS_ID, tenant_id=1)
    with patch(
        'bisheng.datasource.api.endpoints.datasources.DbDatasourceDao.aget_by_id',
        AsyncMock(return_value=existing),
    ), patch(
        'bisheng.datasource.api.endpoints.datasources.DbDatasourceDao.adelete',
        AsyncMock(),
    ) as adelete, patch(
        'bisheng.datasource.api.endpoints.datasources.DbDatasourceTableDao.adelete_by_datasource',
        AsyncMock(),
    ) as adelete_tables:
        try:
            await delete_datasource(datasource_id=DS_ID, login_user=_User())
        except DatasourceInUseError as error:
            return error, adelete, adelete_tables
    return None, adelete, adelete_tables


async def test_a_referenced_datasource_is_not_deleted(monkeypatch):
    error, adelete, adelete_tables = await _delete(
        monkeypatch,
        {'assistants': [{'id': 'a-1', 'name': 'assistant-one'}], 'flows': []},
    )

    assert error is not None
    assert error.code == 11711
    # The counts reach the API caller's message; the page renders its own copy.
    assert '1 assistant' in error.message
    # And nothing was removed — a refusal that half-deletes is worse than none.
    adelete.assert_not_called()
    adelete_tables.assert_not_called()


async def test_a_workflow_reference_alone_also_blocks(monkeypatch):
    error, adelete, _ = await _delete(
        monkeypatch, {'assistants': [], 'flows': [{'id': 'f-1', 'name': 'risk-report'}]}
    )

    assert error is not None
    adelete.assert_not_called()


async def test_an_unreferenced_datasource_is_deleted(monkeypatch):
    error, adelete, adelete_tables = await _delete(monkeypatch, {'assistants': [], 'flows': []})

    assert error is None
    adelete.assert_awaited_once()
    adelete_tables.assert_awaited_once()


# ---------------------------------------------------------------------------
# The endpoint the page asks first
# ---------------------------------------------------------------------------


async def test_references_endpoint_refuses_without_manage_permission(monkeypatch):
    """It returns assistant and workflow NAMES. Answering differently for a
    caller without access would make it a way to probe another tenant."""
    monkeypatch.setattr(PermissionService, 'check', AsyncMock(return_value=False))

    with pytest.raises(DatasourceNotFoundError):
        await list_datasource_references(datasource_id=DS_ID, login_user=_User())


async def test_references_endpoint_returns_both_halves(monkeypatch):
    monkeypatch.setattr(PermissionService, 'check', AsyncMock(return_value=True))
    expected = {'assistants': [{'id': 'a-1', 'name': 'assistant-one'}], 'flows': []}
    monkeypatch.setattr(
        'bisheng.datasource.api.endpoints.datasources.alist_references',
        AsyncMock(return_value=expected),
    )
    with patch(
        'bisheng.datasource.api.endpoints.datasources.DbDatasourceDao.aget_by_id',
        AsyncMock(return_value=SimpleNamespace(id=DS_ID, tenant_id=1)),
    ):
        response = await list_datasource_references(datasource_id=DS_ID, login_user=_User())

    assert response.data == expected
