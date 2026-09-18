"""F057: department ownership resolves to permissions at READ time.

A knowledge library (type=0/1) may belong to one department. Membership of that
department — or of any DESCENDANT of it — grants `can_read`-level access.
Nothing writes FGA tuples for this: `department.member` is a flat relation with
no inheritance and no descent (`core/openfga/authorization_model.py:237`), and
expanding a subtree into tuples would leak on reparent while missing newly
created children, because no department-change hook recomputes resource grants.

The rule therefore lives in `PermissionService._implicit_department_space_member_level`,
evaluated against `department.path`. Direction matters: an ANCESTOR department's
library is visible DOWNWARD to its descendants. `UserDepartment` rows carry only
`department_id`, so the departments themselves must be resolved before the
prefix test.

Scope guard: only `knowledge_library` gets the ownership branch. `knowledge_space`
keeps its existing (different) department rule — it has a real binding table and a
write-time grant path.
"""

from unittest.mock import AsyncMock, patch

import pytest

import bisheng.database.models.department as dept_mod
import bisheng.knowledge.domain.models.department_knowledge_space as dks_mod
from bisheng.database.models.department import Department, DepartmentDao, UserDepartmentDao
from bisheng.knowledge.domain.models.knowledge import KnowledgeDao
from bisheng.permission.domain.schemas.permission_schema import PermissionLevel
from bisheng.permission.domain.services.permission_service import PermissionService

_LIBRARY_DEPT_ID = 30


def _dept(id_: int, path: str) -> Department:
    return Department(id=id_, name=f"dept{id_}", parent_id=None, path=path, tenant_id=1)


def _membership(department_id: int) -> object:
    return type("UD", (), {"department_id": department_id, "user_id": 7, "is_primary": 1})()


def _patch_chain(*, owning_dept, user_dept_rows, membership_ids):
    """Patch the exact DB reads the ownership rule performs, in order.

    `DepartmentDao.aget_by_id` resolves the library's owning department;
    `aget_by_ids` resolves the current user's departments (their `path` is what
    the ancestor test needs).
    """
    return [
        patch.object(
            KnowledgeDao,
            "aquery_by_id",
            new_callable=AsyncMock,
            return_value=type("K", (), {"department_id": _LIBRARY_DEPT_ID})(),
        ),
        patch.object(DepartmentDao, "aget_by_id", new_callable=AsyncMock, return_value=owning_dept),
        patch.object(
            UserDepartmentDao,
            "aget_user_departments",
            new_callable=AsyncMock,
            return_value=[_membership(i) for i in membership_ids],
        ),
        patch.object(DepartmentDao, "aget_by_ids", new_callable=AsyncMock, return_value=user_dept_rows),
    ]


async def _level(object_type: str = "knowledge_library") -> str | None:
    return await PermissionService._implicit_department_space_member_level(7, object_type, "55")


@pytest.mark.asyncio
async def test_descendant_of_owning_department_gets_can_read():
    """A member of a DESCENDANT department sees the ancestor's library."""
    patchers = _patch_chain(
        owning_dept=_dept(_LIBRARY_DEPT_ID, "/1/30/"),  # 技术中心 owns the library
        user_dept_rows=[_dept(31, "/1/30/31/")],  # user is in 研发部, a child
        membership_ids=[31],
    )
    for p in patchers:
        p.start()
    try:
        assert await _level() == PermissionLevel.can_read.value
    finally:
        for p in patchers:
            p.stop()


@pytest.mark.asyncio
async def test_owning_department_itself_gets_can_read():
    """Exact match: the user's own department owns the library."""
    patchers = _patch_chain(
        owning_dept=_dept(_LIBRARY_DEPT_ID, "/1/30/"),
        user_dept_rows=[_dept(30, "/1/30/")],
        membership_ids=[30],
    )
    for p in patchers:
        p.start()
    try:
        assert await _level() == PermissionLevel.can_read.value
    finally:
        for p in patchers:
            p.stop()


@pytest.mark.asyncio
async def test_ancestor_of_owning_department_gets_nothing():
    """Direction guard: an ANCESTOR of the owning department must NOT inherit.

    The flow is downward only — a member of 总公司 does not get 研发部's library
    just because 总公司 is higher in the tree.
    """
    patchers = _patch_chain(
        owning_dept=_dept(_LIBRARY_DEPT_ID, "/1/30/31/"),  # 研发部 owns the library
        user_dept_rows=[_dept(30, "/1/30/")],  # user is in its parent
        membership_ids=[30],
    )
    for p in patchers:
        p.start()
    try:
        assert await _level() is None
    finally:
        for p in patchers:
            p.stop()


@pytest.mark.asyncio
async def test_sibling_department_gets_nothing():
    """No shared chain → no grant."""
    patchers = _patch_chain(
        owning_dept=_dept(_LIBRARY_DEPT_ID, "/1/30/"),
        user_dept_rows=[_dept(40, "/1/40/")],
        membership_ids=[40],
    )
    for p in patchers:
        p.start()
    try:
        assert await _level() is None
    finally:
        for p in patchers:
            p.stop()


@pytest.mark.asyncio
async def test_library_without_department_grants_nothing():
    """NULL department = unclassified → falls through to normal FGA visibility."""
    with patch.object(
        KnowledgeDao, "aquery_by_id", new_callable=AsyncMock, return_value=type("K", (), {"department_id": None})()
    ):
        assert await _level() is None


@pytest.mark.asyncio
async def test_dangling_department_id_grants_nothing():
    """A department deleted underneath the library degrades safely (no grant).

    This is what makes the "deliberately no FK" decision in the F057 migration
    safe: a dangling id behaves exactly like an unclassified library.
    """
    with (
        patch.object(
            KnowledgeDao, "aquery_by_id", new_callable=AsyncMock, return_value=type("K", (), {"department_id": 999})()
        ),
        patch.object(DepartmentDao, "aget_by_id", new_callable=AsyncMock, return_value=None),
    ):
        assert await _level() is None


@pytest.mark.asyncio
async def test_user_without_departments_grants_nothing():
    with (
        patch.object(
            KnowledgeDao,
            "aquery_by_id",
            new_callable=AsyncMock,
            return_value=type("K", (), {"department_id": _LIBRARY_DEPT_ID})(),
        ),
        patch.object(
            DepartmentDao, "aget_by_id", new_callable=AsyncMock, return_value=_dept(_LIBRARY_DEPT_ID, "/1/30/")
        ),
        patch.object(UserDepartmentDao, "aget_user_departments", new_callable=AsyncMock, return_value=[]),
    ):
        assert await _level() is None


@pytest.mark.asyncio
async def test_knowledge_space_scope_is_not_widened():
    """Regression guard: the knowledge_space branch keeps its own semantics."""
    with (
        patch.object(
            dks_mod.DepartmentKnowledgeSpaceDao, "aget_by_space_id", new_callable=AsyncMock, return_value=None
        ),
        patch.object(
            dept_mod.UserDepartmentDao, "aget_user_departments", new_callable=AsyncMock, return_value=[_membership(30)]
        ),
    ):
        assert await _level("knowledge_space") is None


@pytest.mark.asyncio
async def test_unknown_object_type_grants_nothing():
    assert await _level("workflow") is None


@pytest.mark.asyncio
async def test_prefix_match_does_not_leak_across_sibling_ids():
    """`/1/2/` must not prefix-match `/1/20/` — the trailing slash is load-bearing."""
    patchers = _patch_chain(
        owning_dept=_dept(2, "/1/2/"),
        user_dept_rows=[_dept(20, "/1/20/")],
        membership_ids=[20],
    )
    for p in patchers:
        p.start()
    try:
        assert await _level() is None
    finally:
        for p in patchers:
            p.stop()
