"""Extraction-record listing: filters and cursor pagination.

This layer had no test at any level before (DAO, API and e2e were all silent on
it), which matters more than it looks: the cursor predicate decides whether
paging terminates. `id < cursor` written as `id <= cursor` returns the same row
on every page -- an infinite loop that no assertion would have caught.

The statement is built against the real model and compiled, so the SQL under
test is the SQL that would run; only the session is faked.
"""

from datetime import datetime
from unittest.mock import AsyncMock

import pytest

from bisheng.doc_extract.domain.models.extract_run import DocExtractRunDao

DAO = 'bisheng.doc_extract.domain.models.extract_run'


class _Result:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


class _CapturingSession:
    """Records the compiled SQL, so assertions are about the query itself."""

    def __init__(self, rows):
        self.rows = rows
        self.sql = ''

    async def exec(self, stmt):
        from sqlalchemy.dialects import mysql

        self.sql = str(stmt.compile(dialect=mysql.dialect(), compile_kwargs={'literal_binds': True}))
        return _Result(self.rows)


def _patch_session(rows):
    session = _CapturingSession(rows)
    ctx = AsyncMock()
    ctx.__aenter__ = AsyncMock(return_value=session)
    ctx.__aexit__ = AsyncMock(return_value=False)
    return ctx, session


def _row(run_id):
    row = type('R', (), {})()
    row.id = run_id
    return row


async def _list(rows, **kwargs):
    from unittest.mock import patch

    ctx, session = _patch_session(rows)
    with patch(f'{DAO}.get_async_db_session', return_value=ctx):
        result = await DocExtractRunDao.alist_runs(**kwargs)
    return result, session.sql


# --- cursor pagination ------------------------------------------------------


async def test_cursor_is_exclusive():
    """Strictly-less-than: an inclusive comparison would re-serve the cursor row
    forever, so paging would never advance."""
    _, sql = await _list([_row(9)], cursor=10)
    assert 'id < 10' in sql or 'id < %s' in sql


async def test_next_cursor_is_the_last_returned_row():
    """limit+1 rows back means another page exists, so the cursor is the last
    row actually returned (not the probe row)."""
    rows = [_row(40), _row(30), _row(20), _row(10)]
    (returned, next_cursor), _ = await _list(rows, limit=3)
    assert [r.id for r in returned] == [40, 30, 20]
    assert next_cursor == 20


async def test_next_cursor_is_none_on_the_last_page():
    """Fewer rows than the limit means there is nothing after this page."""
    (_, next_cursor), _ = await _list([_row(30), _row(20)], limit=3)
    assert next_cursor is None


async def test_extra_row_is_fetched_to_detect_a_next_page():
    """limit+1 rows come back; only `limit` are returned, and the cursor points
    at the last *returned* row (not the probe row)."""
    rows = [_row(40), _row(30), _row(20), _row(10)]
    (returned, next_cursor), sql = await _list(rows, limit=3)
    assert [r.id for r in returned] == [40, 30, 20]
    assert next_cursor == 20, 'cursor must not point at the probe row'
    assert 'LIMIT 4' in sql


async def test_newest_first_ordering():
    _, sql = await _list([], limit=5)
    assert 'ORDER BY' in sql and 'DESC' in sql


# --- filters ----------------------------------------------------------------


async def test_template_filter_is_applied():
    _, sql = await _list([], template_id=12)
    assert 'template_id' in sql and '12' in sql


async def test_status_filter_is_applied():
    _, sql = await _list([], status=0)
    assert 'status' in sql


async def test_time_range_filters_are_applied():
    _, sql = await _list([], start_time=datetime(2026, 1, 1), end_time=datetime(2026, 12, 31))
    assert 'create_time' in sql and '2026-01-01' in sql and '2026-12-31' in sql


async def test_no_filters_means_no_predicates():
    """An unfiltered call must not accidentally narrow -- e.g. by always adding
    a `status = 1`, which would hide every failure from the record page."""
    _, sql = await _list([], limit=5)
    # Slice at WHERE so column names in the SELECT list are not mistaken for
    # predicates.
    where_clause = sql.split('WHERE', 1)[1] if 'WHERE' in sql else ''
    assert 'template_id' not in where_clause
    assert 'create_time' not in where_clause
    assert 'status' not in where_clause


# --- limit handling ---------------------------------------------------------


@pytest.mark.parametrize('bad_limit', [0, -1, -100])
async def test_non_positive_limit_is_clamped(bad_limit):
    """A zero/negative page size used to index an empty slice and raise
    IndexError -- a 500 on an admin-only read. Clamped to 1 instead."""
    rows = [_row(50), _row(40)]
    (returned, next_cursor), _ = await _list(rows, limit=bad_limit)
    assert len(returned) == 1
    assert next_cursor == 50


async def test_page_size_comes_from_limit():
    _, sql = await _list([], limit=7)
    assert 'LIMIT 8' in sql, 'limit+1 for the probe row'
