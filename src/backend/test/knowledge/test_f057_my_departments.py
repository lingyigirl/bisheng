"""F057: the department tree behind the knowledge page's filter.

`/api/v1/departments/*` cannot serve this. F038 scopes that family to system
admins, department admins and tenant admins, and answers 21009 for everyone else
— so the knowledge page, whose audience is everyone, needs its own read-only
source.

SCOPE: the WHOLE tenant tree, for every caller. An earlier version returned only
the caller's own departments plus their ancestors, on the theory that those are
the only departments whose libraries the caller can see. That made the tree
useless for browsing — an admin landed on a two-node tree, and nobody could see
the shape of the organisation. The per-row permission check is what actually
guards the libraries; the filter only narrows a view. Selecting a department the
caller can read nothing in yields an empty list, same as a genuinely empty one.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from bisheng.database.models.department import Department, DepartmentDao
from bisheng.knowledge.domain.services.knowledge_department_service import KnowledgeDepartmentService

_ACTOR = SimpleNamespace(user_id=7, tenant_id=1)


def _dept(id_: int, path: str, *, parent_id=None, status="active", name=None, sort_order=0) -> Department:
    return Department(
        id=id_,
        dept_id=f"BS@{id_:04x}",
        name=name or f"dept{id_}",
        parent_id=parent_id,
        path=path,
        sort_order=sort_order,
        status=status,
        tenant_id=1,
    )


def _patch(*, layer, with_children=()):
    """Patch the two reads: the layer query and the batched existence query."""
    return [
        patch.object(DepartmentDao, "aget_children", new_callable=AsyncMock, return_value=layer),
        patch.object(
            DepartmentDao,
            "aget_children_existence",
            new_callable=AsyncMock,
            return_value=set(with_children),
        ),
    ]


async def _call(parent_id=None, include_archived=False):
    return await KnowledgeDepartmentService.browse_departments(
        parent_id=parent_id, include_archived=include_archived
    )


@pytest.mark.asyncio
async def test_root_layer_is_returned_to_a_caller_with_no_membership():
    """The whole tree is visible regardless of which departments the caller is in.

    This is the regression that motivated the change: scoping the tree by
    membership gave an admin a two-node tree (their guest department plus the
    root) while the tenant had seven departments.
    """
    roots = [_dept(1, "/1/", name="root"), _dept(2, "/1/2/", name="guest")]
    patchers = _patch(layer=roots)
    for p in patchers:
        p.start()
    try:
        layer = await _call()
        assert [n["id"] for n in layer] == [1, 2]
    finally:
        for p in patchers:
            p.stop()


@pytest.mark.asyncio
async def test_deep_branches_are_reachable():
    """A layer below the root returns its children — no membership filter."""
    children = [_dept(30, "/1/30/", parent_id=1), _dept(40, "/1/40/", parent_id=1)]
    patchers = _patch(layer=children)
    for p in patchers:
        p.start()
    try:
        assert [n["id"] for n in await _call(parent_id=1)] == [30, 40]
    finally:
        for p in patchers:
            p.stop()


@pytest.mark.asyncio
async def test_has_children_comes_from_the_dao_not_the_layer():
    """`has_children` must be a real existence check, not "is in this layer".

    This is the FULL tree, so a node may have children that are archived away and
    therefore absent from any layer the caller will load. Deriving the flag from
    the layer would offer an expand arrow that yields nothing.
    """
    layer = [_dept(30, "/1/30/", parent_id=1), _dept(40, "/1/40/", parent_id=1)]
    patchers = _patch(layer=layer, with_children={30})  # only 30 has children
    for p in patchers:
        p.start()
    try:
        by_id = {n["id"]: n for n in await _call(parent_id=1)}
        assert by_id[30]["has_children"] is True
        assert by_id[40]["has_children"] is False
    finally:
        for p in patchers:
            p.stop()


@pytest.mark.asyncio
async def test_empty_layer_short_circuits_the_existence_query():
    """No nodes → no existence query. An empty org tree should not cost a round-trip."""
    existence = AsyncMock(return_value=set())
    with (
        patch.object(DepartmentDao, "aget_children", new_callable=AsyncMock, return_value=[]),
        patch.object(DepartmentDao, "aget_children_existence", existence),
    ):
        assert await _call() == []
        existence.assert_not_awaited()


@pytest.mark.asyncio
async def test_include_archived_is_forwarded():
    """The management-style `include_archived` flag reaches the DAO unchanged.

    The picker leaves it false, but a caller that wants archived nodes must be
    able to ask — the flag is part of the endpoint's contract.
    """
    children = AsyncMock(return_value=[])
    with (
        patch.object(DepartmentDao, "aget_children", children),
        patch.object(DepartmentDao, "aget_children_existence", new_callable=AsyncMock, return_value=set()),
    ):
        await _call(parent_id=7, include_archived=True)
        children.assert_awaited_once_with(7, include_archived=True)


@pytest.mark.asyncio
async def test_node_shape_matches_the_children_endpoint():
    """The tree component consumes `GET /departments/children`; the shape must match."""
    patchers = _patch(layer=[_dept(1, "/1/")])
    for p in patchers:
        p.start()
    try:
        node = (await _call())[0]
        for key in (
            "id",
            "dept_id",
            "name",
            "parent_id",
            "path",
            "sort_order",
            "source",
            "status",
            "is_tenant_root",
            "mounted_tenant_id",
            "has_children",
            "matched",
            "children",
        ):
            assert key in node, f"missing node field {key!r}"
    finally:
        for p in patchers:
            p.stop()


# ---------------------------------------------------------------------------
# search_departments — server-side, over the same tenant-wide tree
# ---------------------------------------------------------------------------


def _patch_search(*, matches, ancestors=None, with_children=()):
    return [
        patch.object(DepartmentDao, "aget_by_name_like", new_callable=AsyncMock, return_value=matches),
        patch.object(
            DepartmentDao,
            "aget_by_ids",
            new_callable=AsyncMock,
            return_value=ancestors if ancestors is not None else matches,
        ),
        patch.object(
            DepartmentDao,
            "aget_children_existence",
            new_callable=AsyncMock,
            return_value=set(with_children),
        ),
    ]


@pytest.mark.asyncio
async def test_blank_keyword_does_not_query():
    """Whitespace → empty result with NO query: a bare LIKE '%%' is a full scan."""
    lookup = AsyncMock(return_value=[])
    with patch.object(DepartmentDao, "aget_by_name_like", lookup):
        result = await KnowledgeDepartmentService.search_departments("   ")
        assert result == {"roots": [], "total_matches": 0, "truncated": False}
        lookup.assert_not_awaited()


@pytest.mark.asyncio
async def test_matches_below_a_collapsed_node_are_found():
    """The whole point of searching server-side.

    A client filtering its loaded layers only sees what the user expanded; a
    department nested under a collapsed node must still be found by name.
    """
    deep = _dept(31, "/1/30/31/", parent_id=30, name="deep-child")
    parent = _dept(30, "/1/30/", parent_id=1, name="parent")
    root = _dept(1, "/1/", name="root")

    patchers = _patch_search(matches=[deep], ancestors=[root, parent, deep], with_children={1, 30})
    for p in patchers:
        p.start()
    try:
        result = await KnowledgeDepartmentService.search_departments("deep")
        assert result["total_matches"] == 1
        # The hit comes back nested under its ancestors, already expanded.
        assert result["roots"][0]["id"] == 1
        assert result["roots"][0]["children"][0]["id"] == 30
        assert result["roots"][0]["children"][0]["children"][0]["id"] == 31
    finally:
        for p in patchers:
            p.stop()


@pytest.mark.asyncio
async def test_hit_is_flagged_matched():
    """The component highlights `matched` nodes; an unflagged hit looks like a
    plain ancestor and the user cannot tell which node actually matched."""
    hit = _dept(30, "/1/30/", parent_id=1, name="target")

    patchers = _patch_search(matches=[hit], ancestors=[_dept(1, "/1/"), hit])
    for p in patchers:
        p.start()
    try:
        result = await KnowledgeDepartmentService.search_departments("target")
        leaf = result["roots"][0]["children"][0]
        assert leaf["matched"] is True
        assert result["roots"][0]["matched"] is False
    finally:
        for p in patchers:
            p.stop()


@pytest.mark.asyncio
async def test_truncation_is_reported():
    """Over-limit matches must set `truncated` rather than silently dropping hits."""
    matches = [_dept(i, f"/1/{i}/", parent_id=1, name=f"d{i}") for i in range(1, 12)]

    patchers = _patch_search(matches=matches, ancestors=matches)
    for p in patchers:
        p.start()
    try:
        result = await KnowledgeDepartmentService.search_departments("d", limit=10)
        assert result["truncated"] is True, 'an over-limit result did not report truncation'
        assert result["total_matches"] == 10
    finally:
        for p in patchers:
            p.stop()


@pytest.mark.asyncio
async def test_no_matches_returns_empty():
    patchers = _patch_search(matches=[])
    for p in patchers:
        p.start()
    try:
        assert await KnowledgeDepartmentService.search_departments("nothing") == {
            "roots": [],
            "total_matches": 0,
            "truncated": False,
        }
    finally:
        for p in patchers:
            p.stop()
