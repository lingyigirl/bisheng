"""DAO behaviour for F061's four tables.

The session is faked at the lowest layer (the statement is built against the
real model and compiled, so the SQL under test is the SQL that would run), but
the DAOs themselves are NOT mocked. That distinction is the whole point: the
F058 review found three HIGH defects that passed 100+ green tests precisely
because the tests mocked the layer under test, one of them a cross-tenant write.

What is asserted here is therefore the DAO's own contract -- tenant narrowing,
the empty-ids short circuit, and the two-column snapshot refresh that spec
AC-12 depends on.
"""

from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy.dialects import mysql
from sqlalchemy.exc import SQLAlchemyError

from bisheng.datasource.domain.models.dao import (
    DbDatasourceDao,
    DbDatasourceTableDao,
    DbQueryFailureDao,
    DbQueryLogDao,
)


class _Result:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows

    def first(self):
        return self._rows[0] if self._rows else None


class _Session:
    """Captures every statement so assertions are about the SQL, not the calls."""

    def __init__(self, rows=(), scalar_value=0):
        self._rows = list(rows)
        self._scalar = scalar_value
        self.statements = []
        self.updates = []
        self.committed = 0

    async def exec(self, stmt):
        self.statements.append(stmt)
        # `update()` statements carry their SET values on the statement's
        # compiled params rather than in the SQL text (a JSON payload has no
        # literal renderer), so record the column list explicitly.
        if getattr(stmt, 'is_update', False):
            # The compiled params include the WHERE clause's bound values
            # (`id_1`), which are not columns being written. Keep only names
            # that are real columns of the target table.
            params = set(stmt.compile(dialect=mysql.dialect()).params.keys())
            columns = {c.name for c in stmt.table.columns}
            self.updates.append(params & columns)
        return _Result(self._rows)

    async def scalar(self, stmt):
        self.statements.append(stmt)
        return self._scalar

    async def commit(self):
        self.committed += 1

    async def refresh(self, obj):
        return None

    def add(self, obj):
        return None

    async def delete(self, obj):
        return None

    @property
    def sql(self):
        """Compiled SQL, inlining literal values where the dialect can.

        `literal_binds=True` gives readable SQL (`enabled = 1`, `LIMIT 20, 10`)
        for ordinary statements, but raises CompileError on a JSON parameter --
        there is no literal renderer for a JSON type. So it is applied per
        statement and falls back to bound placeholders for the ones that carry
        a JSON payload; those assert on `updates`/`bound_params` instead.
        """
        out = []
        for s in self.statements:
            try:
                out.append(str(s.compile(dialect=mysql.dialect(), compile_kwargs={'literal_binds': True})))
            except Exception:
                out.append(str(s.compile(dialect=mysql.dialect())))
        return out

    @property
    def bound_params(self):
        out = []
        for s in self.statements:
            try:
                out.extend(s.compile(dialect=mysql.dialect()).params.values())
            except Exception:
                continue
        return out


def _patch(session):
    ctx = AsyncMock()
    ctx.__aenter__ = AsyncMock(return_value=session)
    ctx.__aexit__ = AsyncMock(return_value=False)
    return patch(
        'bisheng.datasource.domain.models.dao.get_async_db_session', return_value=ctx
    )


# --- authorization narrowing -------------------------------------------------


@pytest.mark.asyncio
async def test_alist_with_empty_ids_returns_nothing_without_querying():
    """`ids=[]` means "authorized for nothing", which is different from "no
    filter". If the empty list were treated as "no narrowing", a user with no
    grants would be handed every datasource in the tenant."""
    session = _Session(rows=[object()])
    with _patch(session):
        rows, total = await DbDatasourceDao.alist(ids=[])

    assert rows == []
    assert total == 0
    assert session.statements == [], 'must short-circuit before touching the database'


@pytest.mark.asyncio
async def test_alist_with_ids_narrows_the_query():
    session = _Session(rows=[], scalar_value=0)
    with _patch(session):
        await DbDatasourceDao.alist(ids=[3, 7])

    sql = ' '.join(session.sql)
    assert 'db_datasource.id IN' in sql, sql
    assert [3, 7] in session.bound_params, session.bound_params


@pytest.mark.asyncio
async def test_alist_without_ids_applies_no_id_predicate():
    """None means "no narrowing" (platform-admin view); it must not degenerate
    into an empty IN() clause, which would match nothing."""
    session = _Session(rows=[], scalar_value=0)
    with _patch(session):
        await DbDatasourceDao.alist()

    sql = ' '.join(session.sql)
    assert 'db_datasource.id IN' not in sql, sql


@pytest.mark.asyncio
async def test_enabled_only_filters():
    session = _Session(rows=[], scalar_value=0)
    with _patch(session):
        await DbDatasourceDao.alist(enabled_only=True)

    assert 'enabled = 1' in ' '.join(session.sql)


@pytest.mark.asyncio
async def test_name_filters_by_substring():
    """The admin list's filter box. Substring, not identity -- `aget_by_name`
    is the exact-match lookup and the two must not be confused."""
    session = _Session(rows=[], scalar_value=0)
    with _patch(session):
        await DbDatasourceDao.alist(name='订单')

    sql = ' '.join(session.sql)
    assert 'db_datasource.name LIKE' in sql, sql
    # `literal_binds` inlines the pattern too, and SQLAlchemy escapes a literal
    # `%` as `%%` when rendering a bound parameter -- hence the doubled form.
    # The wrapping is what makes it a substring match rather than an equality.
    assert "db_datasource.name LIKE '%%订单%%'" in sql, sql


@pytest.mark.asyncio
async def test_a_blank_name_applies_no_predicate():
    """A blank box must list everything.

    Asserted as an absence because the two plausible mistakes are both silent:
    matching a datasource literally named "" would empty the list as soon as the
    admin clears the box, and a `%`-wrapped empty string (`LIKE '%%'`) returns
    the right rows while quietly meaning something else.
    """
    for blank in ('', '   ', None):
        session = _Session(rows=[], scalar_value=0)
        with _patch(session):
            await DbDatasourceDao.alist(name=blank)
        sql = ' '.join(session.sql)
        assert 'LIKE' not in sql, f'name={blank!r} produced a predicate: {sql}'


# --- pagination --------------------------------------------------------------


@pytest.mark.asyncio
async def test_alist_pages_with_offset_and_limit():
    session = _Session(rows=[], scalar_value=0)
    with _patch(session):
        await DbDatasourceDao.alist(page=3, page_size=10)

    # MySQL renders offset/limit as `LIMIT <offset>, <limit>`; asserting on the
    # two numbers rather than on an `OFFSET` keyword keeps this dialect-agnostic.
    sql = ' '.join(session.sql)
    assert 'LIMIT 20, 10' in sql, sql


@pytest.mark.asyncio
async def test_rows_are_unwrapped_from_the_tenant_filter_wrapper():
    """The tenant hook rewraps SELECTs into Row tuples; returning `row` instead
    of `row[0]` yields a KeyError on first attribute access in production
    (F056's pitfall 11, found only under a real session)."""
    entity = object()
    session = _Session(rows=[(entity,)], scalar_value=1)
    with _patch(session):
        rows, _ = await DbDatasourceDao.alist(ids=[1])

    assert rows == [entity]


# --- the AC-12 guarantee: re-collect must not touch the description ----------


@pytest.mark.asyncio
async def test_snapshot_refresh_updates_only_the_collected_columns():
    """spec AC-12: a re-collect refreshes the structure snapshot and must NOT
    overwrite the admin-written business description.

    Asserted on the generated UPDATE's column list rather than on a call
    count: a whole-row write would pass any "did it update" assertion while
    silently clobbering the description, which is exactly the failure mode
    this guard exists to prevent."""
    session = _Session()
    with _patch(session):
        await DbDatasourceTableDao.areplace_snapshot(
            table_id=5, structure_snapshot=[{'name': 'id', 'type': 'int', 'comment': None}],
            table_comment='客户表',
        )

    assert session.updates, 'expected one UPDATE'
    written = set().union(*session.updates)
    assert written == {'structure_snapshot', 'table_comment'}, written
    assert 'business_desc' not in written, (
        f're-collect must not write business_desc (spec AC-12): {written}'
    )
    assert session.committed == 1


@pytest.mark.asyncio
async def test_business_desc_update_touches_only_that_column():
    session = _Session()
    with _patch(session):
        await DbDatasourceTableDao.aupdate_business_desc(table_id=5, business_desc='存客户主数据')

    written = set().union(*session.updates)
    assert written == {'business_desc'}, (
        f'editing the description must not rewrite the collected snapshot: {written}'
    )


# --- failure trail is best-effort -------------------------------------------


@pytest.mark.asyncio
async def test_failure_record_write_failure_does_not_raise():
    """AC-40: a failure to record a failure must not take down the turn. The
    user has already been given the handled failure message at this point."""
    boom = AsyncMock(side_effect=SQLAlchemyError('disk full'))
    boom.__aenter__ = AsyncMock(side_effect=SQLAlchemyError('disk full'))
    with patch(
        'bisheng.datasource.domain.models.dao.get_async_db_session', return_value=boom
    ):
        # Must not raise.
        await DbQueryFailureDao.acreate_best_effort({'datasource_id': 1})


# --- query log filters -------------------------------------------------------


@pytest.mark.asyncio
async def test_query_log_filters_compose():
    session = _Session(rows=[], scalar_value=0)
    with _patch(session):
        await DbQueryLogDao.alist(datasource_id=4, status='failed')

    sql = ' '.join(session.sql)
    assert 'datasource_id = 4' in sql and "status = 'failed'" in sql, sql
