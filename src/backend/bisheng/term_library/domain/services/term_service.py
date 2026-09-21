
# shows; fullwidth punctuation is correct in them.

"""Term library CRUD and the rules a term must satisfy (F060).

The rules live here rather than in the schema or the endpoint because they span
more than one field, and one of them cannot be a database constraint at all:

  * **Uniqueness spans `standard_term` AND every alias**, per tenant (spec AC-02,
    AC-03). Aliases live inside a JSON column, so no index can reach them. It is
    therefore checked here, and it has to be checked in all four directions —
    new-vs-existing-standard, new-alias-vs-existing-standard,
    new-standard-vs-existing-alias, and new-vs-new — because each is a different
    code path and missing any one lets one string map to two terms.

  * **Nothing here reads or grants permissions.** The library is a tenant-level
    shared configuration, not a controlled resource: read is gated on the
    knowledge menu key and write on `create_knowledge`, both at the endpoint
    (design decision 2). Calling `PermissionService` would be the wrong model.

  * **The department field is display metadata** (contract INV-17). It is
    validated on write only — that the department exists — and it never affects
    what anyone can see or what gets injected. A department archived after a term
    was filed leaves the term alone; the read path simply shows it ungrouped.
"""

from __future__ import annotations

from loguru import logger

from bisheng.common.errcode.term_library import (
    TermDefinitionInvalidError,
    TermDepartmentNotFoundError,
    TermDuplicateError,
    TermLibraryLimitExceededError,
    TermNotFoundError,
)
from bisheng.database.models.department import DepartmentDao
from bisheng.term_library.domain.models.dao import TermLibraryTermDao
from bisheng.term_library.domain.models.term import (
    MAX_DEFINITION_LENGTH,
    MAX_TERMS_PER_TENANT,
    TermLibraryTerm,
)


def _clean_aliases(aliases: list[str] | None) -> list[str]:
    """Trim, drop blanks, and de-duplicate — preserving order.

    A blank alias is REJECTED rather than dropped: it would enter the match
    automaton as the empty string, which matches every question, so the whole
    library would fire at once. That is bad enough to be worth an error rather
    than a silent filter.

    Duplicates within one term are collapsed instead (they are the same alias
    written twice, which is unambiguous), but a duplicate that collides with
    another term's name is a real conflict and is caught by `_assert_unique`.
    """
    cleaned: list[str] = []
    for alias in aliases or []:
        text = (alias or '').strip()
        if not text:
            raise TermDefinitionInvalidError(msg='别名不能为空')
        if text not in cleaned:
            cleaned.append(text)
    return cleaned


def _assert_definition(standard_term: str, definition: str) -> tuple[str, str]:
    """The single-field rules (spec AC-04)."""
    standard = (standard_term or '').strip()
    if not standard:
        raise TermDefinitionInvalidError(msg='标准词不能为空')

    body = (definition or '').strip()
    if not body:
        raise TermDefinitionInvalidError(msg='释义不能为空')
    if len(body) > MAX_DEFINITION_LENGTH:
        raise TermDefinitionInvalidError(
            msg=f'释义不能超过 {MAX_DEFINITION_LENGTH} 字'
        )
    return standard, body


async def _assert_unique(standard: str, aliases: list[str], *, exclude_id: int | None = None) -> None:
    """No string in this term may already name a different term.

    Checks BOTH sides of every existing term — its standard name and its aliases
    — against BOTH sides of the incoming one. Four directions, one rule: after
    this, no string in the library maps to more than one term.

    Reads the whole library rather than querying by name: the alias side is
    inside JSON and unqueryable, the library is capped at 500 rows (AC-06), and
    a partial check would have to be repeated for the alias case anyway.
    """
    existing = await TermLibraryTermDao.alist_all()
    taken: dict[str, int] = {}
    for term in existing:
        if exclude_id is not None and term.id == exclude_id:
            continue
        taken.setdefault(term.standard_term, term.id)
        for alias in term.aliases or []:
            taken.setdefault(alias, term.id)

    for name in (standard, *aliases):
        if name in taken:
            raise TermDuplicateError(msg=f'「{name}」已存在于其它词条')


async def _assert_department(department_id: int | None) -> None:
    """The department must exist. Write path only (AC-15 / INV-17)."""
    if department_id is None:
        return
    department = await DepartmentDao.aget_by_id(int(department_id))
    if department is None or getattr(department, 'status', 'active') != 'active':
        raise TermDepartmentNotFoundError()


async def create_term(
    *,
    user_id: int,
    standard_term: str,
    definition: str,
    aliases: list[str] | None = None,
    department_id: int | None = None,
) -> TermLibraryTerm:
    """Add one term (spec AC-01). Raises rather than returning None on any rule."""
    standard, body = _assert_definition(standard_term, definition)
    cleaned = _clean_aliases(aliases)
    await _assert_unique(standard, cleaned)
    await _assert_department(department_id)

    count = await TermLibraryTermDao.acount()
    if count >= MAX_TERMS_PER_TENANT:
        raise TermLibraryLimitExceededError(
            msg=f'术语库最多 {MAX_TERMS_PER_TENANT} 条'
        )

    term = await TermLibraryTermDao.acreate(
        {
            'standard_term': standard,
            'definition': body,
            'aliases': cleaned,
            'department_id': department_id,
            'create_user_id': user_id,
        }
    )
    logger.info('term created id={} by user_id={}', term.id, user_id)
    return term


async def update_term(
    *,
    term_id: int,
    standard_term: str,
    definition: str,
    aliases: list[str] | None = None,
    department_id: int | None = None,
) -> TermLibraryTerm:
    """Replace one term's editable fields.

    A full replace rather than a partial patch: the uniqueness rule spans the
    standard term and the alias set together, so validating a half-updated row
    would mean reasoning about a state that never exists in the database. The
    editor always sends the whole term, which is also what the UI holds.
    """
    term = await TermLibraryTermDao.aget_by_id(term_id)
    if term is None:
        raise TermNotFoundError()

    standard, body = _assert_definition(standard_term, definition)
    cleaned = _clean_aliases(aliases)
    await _assert_unique(standard, cleaned, exclude_id=term_id)
    await _assert_department(department_id)

    updated = await TermLibraryTermDao.aupdate(
        term,
        {
            'standard_term': standard,
            'definition': body,
            'aliases': cleaned,
            'department_id': department_id,
        },
    )
    logger.info('term updated id={}', term_id)
    return updated


async def delete_term(term_id: int) -> None:
    """Hard delete (spec AC-01).

    No soft-delete and no history requirement: the block an answer carried is
    built per turn and stored nowhere (contract INV-18), so there is nothing that
    deleting a term could make unreadable. The history keeps its answers exactly
    as they were.
    """
    term = await TermLibraryTermDao.aget_by_id(term_id)
    if term is None:
        raise TermNotFoundError()
    await TermLibraryTermDao.adelete(term)
    logger.info('term deleted id={}', term_id)


async def list_terms(
    *,
    department_id: int | None = None,
    include_sub_departments: bool = True,
    page: int = 1,
    page_size: int = 20,
) -> tuple[list[TermLibraryTerm], int]:
    """Page the library, optionally narrowed to a department subtree (AC-13).

    The department narrows the LIST only. It grants nothing and it has no effect
    on what gets injected — the injection path reads the whole library regardless
    (contract INV-17).

    `None` means "no filter" and `[]` means "nothing matches"; the two must not
    be interchanged. An unresolvable department therefore resolves to `[]`, not
    to `None`: widening to the whole tenant on a filter that matched no
    department is how an admin ends up looking at another department's terms
    while believing they are scoped.
    """
    department_ids: list[int] | None = None
    if department_id is not None:
        if include_sub_departments:
            department = await DepartmentDao.aget_by_id(int(department_id))
            if department is None or not getattr(department, 'path', None):
                department_ids = []
            else:
                department_ids = await DepartmentDao.aget_subtree_ids(str(department.path))
        else:
            department_ids = [int(department_id)]

    return await TermLibraryTermDao.alist_page(
        department_ids=department_ids, page=page, page_size=page_size
    )
