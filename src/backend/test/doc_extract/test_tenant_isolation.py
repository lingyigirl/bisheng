"""Regression tests for cross-tenant isolation of extraction templates.

The bug these pin down is subtle and was invisible to every other test in this
package, because they all mock the DAO:

  For a Child tenant the global listener's SELECT filter is the IN-list
  ``WHERE tenant_id IN (child, ROOT)`` -- correct for shared resources, wrong
  for tenant-level config. So a lookup by id would return a **Root** template.
  The listener rewrites only SELECTs
  (``tenant_filter.py``: ``if not orm_execute_state.is_select: return``), and
  ``before_flush`` only fills ``session.new``, so the dirty-flush UPDATE that
  followed carried no tenant predicate at all. A Child Admin passing a small
  integer id could therefore rewrite or soft-delete a Root template -- and Root's
  later extractions would run on the attacker's field definitions.

Two independent defences are asserted here: the reads are narrowed with
``strict_tenant_filter()``, and the write paths re-check ownership.
"""

from unittest.mock import AsyncMock, patch

import pytest
from sqlmodel import select

from bisheng.core.context.tenant import current_tenant_id
from bisheng.doc_extract.domain.models.extract_template import (
    DocExtractTemplate,
    DocExtractTemplateDao,
    _belongs_to_current_tenant,
)

DAO = 'bisheng.doc_extract.domain.models.extract_template'


def _template(tenant_id: int):
    row = type('T', (), {})()
    row.id = 1
    row.tenant_id = tenant_id
    row.name = '营业执照'
    row.doc_type = 'business_license'
    row.is_deleted = 0
    return row


@pytest.fixture()
def as_child_tenant():
    """Run the body as Child tenant 2."""
    current_tenant_id.set(2)
    yield 2
    current_tenant_id.set(None)



def _session_returning(row):
    """A fake async session whose `await exec(...)` yields `row` from .first().

    `exec` is awaited but its result's `.first()` is sync -- AsyncMock would
    make both coroutines and the code under test would await the wrong one.
    """
    result = type('R', (), {'first': lambda self: row, 'all': lambda self: []})()
    session = AsyncMock()
    session.exec = AsyncMock(return_value=result)
    ctx = AsyncMock()
    ctx.__aenter__ = AsyncMock(return_value=session)
    ctx.__aexit__ = AsyncMock(return_value=False)
    return ctx


# --- defence 1: ownership check ---------------------------------------------


def test_template_of_another_tenant_is_not_owned(as_child_tenant):
    assert _belongs_to_current_tenant(_template(tenant_id=1)) is False


def test_template_of_the_current_tenant_is_owned(as_child_tenant):
    assert _belongs_to_current_tenant(_template(tenant_id=2)) is True


def test_no_tenant_context_allows_internal_callers():
    """System/background callers have no ambient tenant; the narrowed read has
    already limited what they could have loaded, so this is not a hole."""
    current_tenant_id.set(None)
    assert _belongs_to_current_tenant(_template(tenant_id=1)) is True


# --- defence 1 applied on the write paths -----------------------------------


async def test_update_refuses_a_row_from_another_tenant(as_child_tenant):
    """The decisive case: the SELECT (mocked here as the un-narrowed listener
    would have returned it) yields a Root row, and the write must still not
    happen."""
    with (
        patch(f'{DAO}.get_async_db_session') as session_ctx,
        patch(f'{DAO}.DocExtractTemplateDao._live_stmt',
              return_value=select(DocExtractTemplate).where(DocExtractTemplate.is_deleted == 0)),
    ):
        ctx = _session_returning(_template(tenant_id=1))
        session_ctx.return_value = ctx

        result = await DocExtractTemplateDao.aupdate(1, {'name': 'hijacked'})

    assert result is None, 'a template owned by another tenant must not be updated'
    ctx.__aenter__.return_value.commit.assert_not_awaited()


async def test_soft_delete_refuses_a_row_from_another_tenant(as_child_tenant):
    with (
        patch(f'{DAO}.get_async_db_session') as session_ctx,
        patch(f'{DAO}.DocExtractTemplateDao._live_stmt',
              return_value=select(DocExtractTemplate).where(DocExtractTemplate.is_deleted == 0)),
    ):
        ctx = _session_returning(_template(tenant_id=1))
        session_ctx.return_value = ctx

        deleted = await DocExtractTemplateDao.asoft_delete(1)

    assert deleted is False, 'a template owned by another tenant must not be deleted'
    ctx.__aenter__.return_value.commit.assert_not_awaited()


async def test_update_proceeds_for_own_tenant(as_child_tenant):
    """Guard against over-correction: the normal path must still work."""
    own = _template(tenant_id=2)
    with (
        patch(f'{DAO}.get_async_db_session') as session_ctx,
        patch(f'{DAO}.DocExtractTemplateDao._live_stmt',
              return_value=select(DocExtractTemplate).where(DocExtractTemplate.is_deleted == 0)),
    ):
        ctx = _session_returning(own)
        session_ctx.return_value = ctx

        result = await DocExtractTemplateDao.aupdate(1, {'name': 'renamed'})

    assert result is own
    assert own.name == 'renamed'
    ctx.__aenter__.return_value.commit.assert_awaited()


# --- defence 2: the reads are narrowed --------------------------------------


async def test_reads_run_inside_strict_tenant_filter():
    """Without this, the IN-list leaks Root's templates into a Child's list and
    into the classification candidate set (AC-17 / AC-35)."""
    from bisheng.core.context.tenant import is_strict_tenant_filter

    seen = {}

    async def _record(*args, **kwargs):
        seen['strict'] = is_strict_tenant_filter()
        return type('R', (), {'first': lambda self: None, 'all': lambda self: []})()

    with (
        patch(f'{DAO}.get_async_db_session') as session_ctx,
        patch(f'{DAO}.DocExtractTemplateDao._live_stmt',
              return_value=select(DocExtractTemplate).where(DocExtractTemplate.is_deleted == 0)),
    ):
        session = AsyncMock()
        session.exec.side_effect = _record
        session_ctx.return_value.__aenter__.return_value = session

        await DocExtractTemplateDao.aget_by_id(1)
        assert seen['strict'] is True, 'aget_by_id must narrow the tenant filter'

        await DocExtractTemplateDao.aget_by_doc_type('business_license')
        assert seen['strict'] is True, 'aget_by_doc_type must narrow the tenant filter'

        await DocExtractTemplateDao.alist_templates(enabled_only=True)
        assert seen['strict'] is True, 'alist_templates must narrow the tenant filter'
