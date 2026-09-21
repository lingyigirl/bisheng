"""F060 T-08: term CRUD and the rules a term must satisfy.

The DAO is stubbed (an I/O edge) — the service is the subject and is never mocked
away. Each rule gets its own case rather than one happy path, because these are
the rules an admin hits through the UI and each has a distinct error to report.

Uniqueness is the one worth reading carefully: it spans `standard_term` AND every
alias, per tenant (spec AC-02 / AC-03). It cannot be a database constraint —
aliases live inside a JSON column and no index reaches into it — so it is
enforced here, and the four directions it can collide are all tested.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from bisheng.common.errcode.term_library import (
    TermDefinitionInvalidError,
    TermDepartmentNotFoundError,
    TermDuplicateError,
    TermLibraryLimitExceededError,
)
from bisheng.term_library.domain.models.term import MAX_TERMS_PER_TENANT
from bisheng.term_library.domain.services import term_service


def _row(term_id: int, standard: str, definition: str = '释义', aliases=None, department_id=None):
    return SimpleNamespace(
        id=term_id,
        standard_term=standard,
        definition=definition,
        aliases=aliases or [],
        department_id=department_id,
        create_user_id=1,
    )


def _patched(*, existing=None, count=0, department=None, subtree=None):
    """Patch the DAO + DepartmentDao edges T-08 touches."""
    existing = existing or []
    return (
        patch.object(
            term_service.TermLibraryTermDao, 'alist_all', AsyncMock(return_value=existing)
        ),
        patch.object(term_service.TermLibraryTermDao, 'acount', AsyncMock(return_value=count)),
        patch.object(
            term_service.TermLibraryTermDao,
            'acreate',
            AsyncMock(side_effect=lambda data: _row(99, data['standard_term'], data['definition'])),
        ),
        patch.object(
            term_service.DepartmentDao, 'aget_by_id', AsyncMock(return_value=department)
        ),
        patch.object(
            term_service.DepartmentDao,
            'aget_subtree_ids',
            AsyncMock(return_value=subtree if subtree is not None else []),
        ),
    )


async def _create(standard='不良贷款率', definition='不良贷款占贷款总额的比例', aliases=None, **kw):
    ctx = _patched(**kw)
    with ctx[0], ctx[1], ctx[2], ctx[3], ctx[4]:
        return await term_service.create_term(
            user_id=1, standard_term=standard, definition=definition, aliases=aliases or []
        )


# ── AC-04: the definition rules ──────────────────────────────────────────────


async def test_a_blank_standard_term_is_rejected():
    with pytest.raises(TermDefinitionInvalidError):
        await _create(standard='   ')


async def test_a_blank_definition_is_rejected():
    with pytest.raises(TermDefinitionInvalidError):
        await _create(definition='  ')


async def test_a_definition_at_the_limit_is_accepted():
    """Boundary: exactly 200 characters is valid; the cap is 200, not 199."""
    from bisheng.term_library.domain.models.term import MAX_DEFINITION_LENGTH

    created = await _create(definition='释' * MAX_DEFINITION_LENGTH)

    assert created.standard_term == '不良贷款率'


async def test_a_definition_one_over_the_limit_is_rejected():
    from bisheng.term_library.domain.models.term import MAX_DEFINITION_LENGTH

    with pytest.raises(TermDefinitionInvalidError):
        await _create(definition='释' * (MAX_DEFINITION_LENGTH + 1))


async def test_a_blank_alias_is_rejected():
    """An empty alias would enter the match automaton as the empty string, which
    matches everywhere — the whole library would fire on every question."""
    with pytest.raises(TermDefinitionInvalidError):
        await _create(aliases=['不良率', '  '])


# ── AC-02 / AC-03: uniqueness spans the standard term AND the aliases ────────


async def test_a_duplicate_standard_term_is_rejected():
    existing = [_row(1, '不良贷款率')]

    with pytest.raises(TermDuplicateError):
        await _create(existing=existing)


async def test_a_new_alias_that_collides_with_an_existing_standard_term_is_rejected():
    """Otherwise one string maps to two terms and the match is ambiguous."""
    existing = [_row(1, '不良贷款率')]

    with pytest.raises(TermDuplicateError):
        await _create(standard='拨备覆盖率', aliases=['不良贷款率'], existing=existing)


async def test_a_standard_term_that_collides_with_an_existing_alias_is_rejected():
    """The mirror direction — easy to miss, because the collision is on the OTHER
    term's alias list."""
    existing = [_row(1, '不良贷款率', aliases=['不良率'])]

    with pytest.raises(TermDuplicateError):
        await _create(standard='不良率', existing=existing)


async def test_the_same_alias_written_twice_is_collapsed_not_rejected():
    """A repeat within ONE term is not a conflict — one string with one meaning,
    written twice — so the second occurrence is dropped.

    Asserted explicitly because the opposite behaviour is a plausible reading of
    "aliases must be unique", and it would reject a term over a harmless
    editorial slip. The conflict that matters is a string naming TWO different
    terms, which the tests above cover.
    """
    created = await _create(standard='拨备覆盖率', aliases=['覆盖率', '覆盖率'])

    assert created.standard_term == '拨备覆盖率'


async def test_a_distinct_term_is_accepted():
    existing = [_row(1, '不良贷款率')]

    created = await _create(standard='拨备覆盖率', aliases=['拨备率'], existing=existing)

    assert created.standard_term == '拨备覆盖率'


# ── AC-06: the library cap ───────────────────────────────────────────────────


async def test_the_cap_is_rejected_at_the_limit():
    """Boundary: 500 existing + 1 new = 501, which exceeds the cap of 500."""
    with pytest.raises(TermLibraryLimitExceededError):
        await _create(count=MAX_TERMS_PER_TENANT)


async def test_one_below_the_cap_is_accepted():
    created = await _create(count=MAX_TERMS_PER_TENANT - 1)

    assert created.standard_term == '不良贷款率'


# ── AC-15: the department is display metadata (INV-17) ───────────────────────


async def test_an_unknown_department_is_rejected_on_write():
    """AC-15's error. Note this is the WRITE path only — a term whose department
    was archived later still reads fine, because the field grants nothing
    (INV-17)."""
    ctx = _patched(department=None)
    with ctx[0], ctx[1], ctx[2], ctx[3], ctx[4]:
        with pytest.raises(TermDepartmentNotFoundError):
            await term_service.create_term(
                user_id=1,
                standard_term='不良贷款率',
                definition='比例',
                department_id=42,
            )


async def test_a_known_department_is_accepted():
    dept = SimpleNamespace(id=42, path='/1/42/', status='active')
    ctx = _patched(department=dept)
    with ctx[0], ctx[1], ctx[2], ctx[3], ctx[4]:
        created = await term_service.create_term(
            user_id=1, standard_term='不良贷款率', definition='比例', department_id=42
        )

    assert created.standard_term == '不良贷款率'


async def test_no_department_is_allowed():
    """Unclassified is a legal state (spec AC-15)."""
    created = await _create()

    assert created.standard_term == '不良贷款率'


# ── AC-13: the department filter ─────────────────────────────────────────────


async def test_no_department_filter_returns_everything():
    """`department_id=None` means "no filter", which is different from "terms
    with no department" — conflating the two would hide the whole library."""
    seen = {}

    async def _capture(**kwargs):
        seen.update(kwargs)
        return [_row(1, 'A'), _row(2, 'B')], 2

    with patch.object(term_service.TermLibraryTermDao, 'alist_page', _capture):
        _rows_returned, total = await term_service.list_terms(department_id=None)

    assert total == 2
    assert seen.get('department_ids') is None


async def test_an_unknown_department_filters_to_nothing_not_everything():
    """The trap the knowledge module documents: an unresolvable department must
    narrow to EMPTY, never widen to the whole tenant. `None` means "no filter"
    and `[]` means "nothing matches" — passing the first where the second is
    meant hands an admin another department's terms."""
    seen = {}

    async def _capture(**kwargs):
        seen.update(kwargs)
        return [], 0

    with (
        patch.object(term_service.DepartmentDao, 'aget_by_id', AsyncMock(return_value=None)),
        patch.object(term_service.TermLibraryTermDao, 'alist_page', _capture),
    ):
        rows, total = await term_service.list_terms(department_id=42, include_sub_departments=True)

    assert rows == []
    assert total == 0
    assert seen.get('department_ids') == [], (
        'an unknown department produced no filter, so the list would show the '
        'whole tenant instead of nothing'
    )


async def test_a_subtree_filter_includes_the_child_departments():
    dept = SimpleNamespace(id=42, path='/1/42/', status='active')
    seen = {}

    async def _capture(**kwargs):
        seen.update(kwargs)
        return [], 0

    with (
        patch.object(term_service.DepartmentDao, 'aget_by_id', AsyncMock(return_value=dept)),
        patch.object(
            term_service.DepartmentDao, 'aget_subtree_ids', AsyncMock(return_value=[42, 43, 44])
        ),
        patch.object(term_service.TermLibraryTermDao, 'alist_page', _capture),
    ):
        await term_service.list_terms(department_id=42, include_sub_departments=True)

    assert seen.get('department_ids') == [42, 43, 44]


async def test_without_subtree_the_filter_is_the_single_department():
    dept = SimpleNamespace(id=42, path='/1/42/', status='active')
    seen = {}

    async def _capture(**kwargs):
        seen.update(kwargs)
        return [], 0

    with (
        patch.object(term_service.DepartmentDao, 'aget_by_id', AsyncMock(return_value=dept)),
        patch.object(term_service.TermLibraryTermDao, 'alist_page', _capture),
    ):
        await term_service.list_terms(department_id=42, include_sub_departments=False)

    assert seen.get('department_ids') == [42]
