"""F057: the per-department library counts behind the tree badges.

AC-13 is the reason this file exists: a badge must not let anyone infer the
existence of a library they cannot open. That makes the permission filter on
this path load-bearing rather than cosmetic, and it had no unit coverage — the
endpoint was only ever exercised end-to-end as an admin, where every library is
visible and the filter does nothing.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from bisheng.database.models.department import Department, DepartmentDao
from bisheng.knowledge.domain.services.knowledge_department_service import KnowledgeDepartmentService
from bisheng.knowledge.domain.services.knowledge_permission_service import KnowledgePermissionService

_ACTOR = SimpleNamespace(user_id=7, tenant_id=1)
_TYPE = SimpleNamespace(value=0)


def _dept(id_: int, path: str, parent_id=None) -> Department:
    return Department(
        id=id_,
        dept_id=f"BS@{id_:04x}",
        name=f"dept{id_}",
        parent_id=parent_id,
        path=path,
        sort_order=0,
        status="active",
        tenant_id=1,
    )


class _Rows:
    """Stand-in for the session.exec(...) result of the id/department_id query."""

    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


class _Session:
    def __init__(self, rows):
        self._rows = rows

    async def exec(self, _stmt):
        return _Rows(self._rows)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


def _patch(*, owned, departments, visible):
    """Patch the three reads: owners, department rows, and the permission filter.

    `visible` is what the permission filter returns — the set of library ids the
    caller may actually open. Everything else is visible to them only through a
    badge, which is precisely what AC-13 forbids.

    The session factory is patched at `bisheng.core.database`, its DEFINITION
    site: the service imports it inside the function body, so patching an
    attribute on the service module would miss.
    """

    def _session_factory():
        return _Session(owned)

    return [
        patch("bisheng.core.database.get_async_db_session", _session_factory),
        patch.object(DepartmentDao, "aget_by_ids", new_callable=AsyncMock, return_value=departments),
        patch.object(
            KnowledgePermissionService,
            "filter_knowledge_ids_by_permission_async",
            new_callable=AsyncMock,
            return_value=visible,
        ),
    ]


async def _call():
    return await KnowledgeDepartmentService.department_stats(
        login_user=_ACTOR, knowledge_type=_TYPE, permission_id="view_kb"
    )


@pytest.mark.asyncio
async def test_counts_roll_up_the_subtree():
    """A parent's badge includes its descendants' libraries.

    The badge has to match what clicking the node shows, and the filter is
    subtree-inclusive by default — so the roll-up is part of the contract, not a
    nicety.
    """
    tech = _dept(30, "/1/30/")
    rd = _dept(31, "/1/30/31/", parent_id=30)
    owned = [(101, 30), (102, 31)]

    patchers = _patch(owned=owned, departments=[tech, rd], visible={101, 102})
    for p in patchers:
        p.start()
    try:
        by_id = {r["department_id"]: r["count"] for r in await _call()}
        assert by_id[31] == 1, by_id
        assert by_id[30] == 2, f'parent must include the child library: {by_id}'
    finally:
        for p in patchers:
            p.stop()


@pytest.mark.asyncio
async def test_invisible_libraries_are_not_counted():
    """AC-13: a library the caller cannot open must not move any badge.

    This is the whole point of the permission filter on this path. Without it the
    caller could read a library's existence — and its department — straight off
    the number.
    """
    tech = _dept(30, "/1/30/")
    owned = [(101, 30), (102, 30)]

    patchers = _patch(owned=owned, departments=[tech], visible={101})  # 102 not visible
    for p in patchers:
        p.start()
    try:
        by_id = {r["department_id"]: r["count"] for r in await _call()}
        assert by_id[30] == 1, f'an invisible library was counted: {by_id}'
    finally:
        for p in patchers:
            p.stop()


@pytest.mark.asyncio
async def test_departments_with_nothing_visible_are_omitted():
    """A department the caller can see nothing in must not appear at all.

    Returning it with `count: 0` leaks two facts the caller is not entitled to:
    that the department exists, and that it owns at least one library of this
    type. The frontend skips falsy counts anyway, so omitting costs nothing.
    """
    mine = _dept(30, "/1/30/")
    theirs = _dept(40, "/1/40/")
    owned = [(101, 30), (201, 40)]

    patchers = _patch(owned=owned, departments=[mine, theirs], visible={101})
    for p in patchers:
        p.start()
    try:
        ids = {r["department_id"] for r in await _call()}
        assert 30 in ids, 'the caller\'s own department must still be reported'
        assert 40 not in ids, f'a department the caller sees nothing in was disclosed: {ids}'
    finally:
        for p in patchers:
            p.stop()


@pytest.mark.asyncio
async def test_no_owned_libraries_returns_empty():
    """No owned libraries at all → no rows, no error."""
    patchers = _patch(owned=[], departments=[], visible=set())
    for p in patchers:
        p.start()
    try:
        assert await _call() == []
    finally:
        for p in patchers:
            p.stop()
