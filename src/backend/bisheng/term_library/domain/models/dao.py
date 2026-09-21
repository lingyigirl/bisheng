"""DAO for the term library table (F060).

**Every read runs inside ``strict_tenant_filter()``.** The term library is
tenant-scoped configuration (contract INV-16): a tenant's terms must never be
visible to — or worse, injected into the conversations of — another tenant. The
global listener's default for a Child tenant is the IN-list
``WHERE tenant_id IN (child, ROOT)``, which is right for *shared* resources and
wrong here, so the narrowing has to be explicit.

The same trap F058 and F061 documented applies: the listener filters SELECTs
only. A read that returns a Root row, followed by a dirty-flush UPDATE on that
instance, is never re-checked — and ids are small integers, so guessing one is
trivial. The narrowing therefore happens on the read that produced the instance,
not on the write that follows it.

Ownership note (contract table 1): this feature is the only writer of
``term_library_term``. Nothing else in the codebase may insert, update or delete
these rows.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import func, select

from bisheng.core.context.tenant import strict_tenant_filter
from bisheng.core.database import get_async_db_session
from bisheng.term_library.domain.models.term import TermLibraryTerm


def _rows(result) -> list:
    """Unwrap ORM results.

    The tenant filter's ``do_orm_execute`` hook rewraps SELECTs into Row tuples
    whose single element is the entity, so accessing the entity directly raises
    KeyError — the failure F056 hit in production and recorded as its pitfall 11.
    ``row[0]`` is correct whether or not the hook ran.
    """
    return [row[0] for row in result.all()]


class TermLibraryTermDao:
    @classmethod
    async def alist_all(cls) -> list[TermLibraryTerm]:
        """Every term of the current tenant, newest first.

        Returns the WHOLE library rather than a page: the caller is the injection
        path, which needs the complete set to build its match automaton, and the
        library is capped at 500 rows (spec AC-06). The admin list is a different
        caller with a different contract — it pages (T-08).

        No `tenant_id` argument: the filter supplies it from the request context.
        A caller passing one explicitly could pass someone else's.
        """
        with strict_tenant_filter():
            async with get_async_db_session() as session:
                result = await session.exec(
                    select(TermLibraryTerm).order_by(TermLibraryTerm.id.desc())
                )
                return _rows(result)

    @classmethod
    async def acount(cls) -> int:
        """How many terms the current tenant has (spec AC-06's cap check)."""
        with strict_tenant_filter():
            async with get_async_db_session() as session:
                result = await session.exec(select(func.count()).select_from(TermLibraryTerm))
                return int(result.one()[0])

    @classmethod
    async def acreate(cls, data: dict[str, Any]) -> TermLibraryTerm:
        term = TermLibraryTerm(**data)
        async with get_async_db_session() as session:
            session.add(term)
            await session.commit()
            await session.refresh(term)
            return term

    @classmethod
    async def aget_by_id(cls, term_id: int) -> TermLibraryTerm | None:
        with strict_tenant_filter():
            async with get_async_db_session() as session:
                result = await session.exec(
                    select(TermLibraryTerm).where(TermLibraryTerm.id == term_id)
                )
                rows = _rows(result)
                return rows[0] if rows else None

    @classmethod
    async def aupdate(cls, term: TermLibraryTerm, data: dict[str, Any]) -> TermLibraryTerm:
        for key, value in data.items():
            setattr(term, key, value)
        async with get_async_db_session() as session:
            session.add(term)
            await session.commit()
            await session.refresh(term)
            return term

    @classmethod
    async def adelete(cls, term: TermLibraryTerm) -> None:
        async with get_async_db_session() as session:
            await session.delete(term)
            await session.commit()

    @classmethod
    async def adelete_these(cls, terms: list[TermLibraryTerm]) -> int:
        """Delete exactly the given rows; returns how many.

        Used by the OVERWRITE import mode (spec AC-08), which passes what
        `alist_all` returned — i.e. already narrowed to this tenant.

        **Deliberately not a bulk `delete(TermLibraryTerm)`.** The tenant filter
        intercepts SELECTs only (`tenant_filter.py`: `if not
        orm_execute_state.is_select: return`), so a statement-level DELETE would
        carry no tenant predicate and remove EVERY tenant's library. Deleting
        instances that came through a filtered read is the safe shape, and it is
        the same reasoning that makes `aget_by_id` + mutate the pattern used
        everywhere else here.
        """
        if not terms:
            return 0
        async with get_async_db_session() as session:
            for term in terms:
                await session.delete(term)
            await session.commit()
        return len(terms)
