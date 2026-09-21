"""F062: the tenant-scoped department mapping cache.

`nearest_org_path` needs a `{dept_id: (parent_id, path, is_department)}` mapping
for the whole org tree. Loading it per permission check would be ruinous — the
list path runs the check per row, in 50-row batches, and `department_stats` runs
it for EVERY department-owned library. So the mapping is loaded once per tenant,
lazily, and cached.

Three properties are non-negotiable and each gets a test here, because each fails
SILENTLY if broken:

1. **Tenant isolation.** Department ids are only unique WITHIN a tenant. A cache
   keyed by dept id alone would let tenant A's lookup resolve against tenant B's
   tree — a cross-tenant read. This is a C3 violation, and it would look like an
   ordinary permission grant.
2. **Large-tenant degradation.** The target deployment is ~500-1000 nodes, but
   this repo has seen 3w+ departments (`department_service.py:1518`), where
   loading the whole table is the documented main bottleneck (DM8 bind-param
   serialisation). Past a threshold the loader must fall back instead of
   materialising.
3. **Fail-closed on unavailability.** If the mapping cannot be built (DB down,
   cache unreachable), the caller must get "cannot evaluate" — never an empty
   mapping that reads as "no orgs anywhere" and grants against the wrong path.

覆盖 AC: AC-19
"""

from unittest.mock import AsyncMock, patch

import pytest

from bisheng.database.models.department import Department
from bisheng.permission.domain.services.department_org_domain import (
    MAX_CACHEABLE_DEPARTMENTS,
    build_department_mapping,
    clear_department_mapping_cache,
    get_department_mapping,
    nearest_org_path,
)


@pytest.fixture(autouse=True)
def _clean_cache():
    """Each test starts from a cold cache — the cache is process-global."""
    clear_department_mapping_cache()
    yield
    clear_department_mapping_cache()


def _dept(id_: int, parent_id: int | None, path: str, is_department: int = 0, tenant_id: int = 1) -> Department:
    return Department(
        id=id_,
        dept_id=f"BS@{id_}",
        name=f"dept{id_}",
        parent_id=parent_id,
        path=path,
        tenant_id=tenant_id,
        status="active",
        is_department=is_department,
    )


# ---------------------------------------------------------------------------
# 1. Tenant isolation — the C3 gate
# ---------------------------------------------------------------------------


async def test_same_dept_id_in_two_tenants_resolves_independently():
    """Tenant B's tree must never be consulted for tenant A's lookup.

    Both tenants use ids 1 and 4, but with DIFFERENT shapes: in tenant 1 node 4
    is an 机构; in tenant 2 node 4 is a 部门 under an 机构 root. A cache keyed by
    dept id alone returns whichever tenant loaded first — so the assertion below
    (each tenant sees its OWN shape) is exactly what a missing tenant key breaks.
    """
    tenant1_rows = [
        _dept(1, None, "/1/", is_department=0, tenant_id=1),
        _dept(4, 1, "/1/4/", is_department=0, tenant_id=1),  # 机构
    ]
    tenant2_rows = [
        _dept(1, None, "/1/", is_department=0, tenant_id=2),
        _dept(4, 1, "/1/4/", is_department=1, tenant_id=2),  # 部门!
    ]

    async def _by_tenant(tenant_id: int):
        return tenant1_rows if tenant_id == 1 else tenant2_rows

    with patch(
        "bisheng.permission.domain.services.department_org_domain._load_departments",
        new=AsyncMock(side_effect=_by_tenant),
    ):
        mapping1 = await get_department_mapping(1)
        mapping2 = await get_department_mapping(2)

    # tenant 1: node 4 is an org -> its own path
    assert mapping1[4][2] == 0, "tenant 1 node 4 must still read as 机构"
    # tenant 2: node 4 is a department -> the flag differs
    assert mapping2[4][2] == 1, "tenant 2 node 4 must read as 部门, not tenant 1's value"


async def test_second_call_for_a_different_tenant_does_not_reuse_the_first():
    """The cache must be keyed per tenant, not "the last tenant loaded"."""
    calls: list[int] = []

    async def _by_tenant(tenant_id: int):
        calls.append(tenant_id)
        return [_dept(1, None, "/1/", is_department=0, tenant_id=tenant_id)]

    with patch(
        "bisheng.permission.domain.services.department_org_domain._load_departments",
        new=AsyncMock(side_effect=_by_tenant),
    ):
        await get_department_mapping(1)
        await get_department_mapping(2)
        await get_department_mapping(1)  # cached

    assert calls == [1, 2], f"tenant 1 should be loaded once, got loads={calls}"


async def test_same_tenant_is_loaded_once_and_served_from_cache():
    loader = AsyncMock(return_value=[_dept(1, None, "/1/")])
    with patch(
        "bisheng.permission.domain.services.department_org_domain._load_departments",
        new=loader,
    ):
        for _ in range(5):
            await get_department_mapping(1)
    assert loader.await_count == 1, "repeated lookups for one tenant must hit the cache"


# ---------------------------------------------------------------------------
# 2. Large-tenant degradation
# ---------------------------------------------------------------------------


async def test_oversized_tenant_does_not_materialise_the_whole_table():
    """Past the threshold the loader must refuse to build a full mapping.

    Returning `None` (not `{}`) is the contract: `{}` is a VALID mapping meaning
    "this tenant has no departments", which would silently deny everything. None
    means "cannot answer from cache", which the caller handles by falling back to
    per-node traversal.
    """
    oversized = [
        _dept(i, 1 if i > 1 else None, f"/1/{i}/", is_department=1, tenant_id=1)
        for i in range(1, MAX_CACHEABLE_DEPARTMENTS + 2)
    ]
    with patch(
        "bisheng.permission.domain.services.department_org_domain._load_departments",
        new=AsyncMock(return_value=oversized),
    ):
        mapping = await get_department_mapping(1)

    assert mapping is None, (
        f"{len(oversized)} departments exceeds MAX_CACHEABLE_DEPARTMENTS="
        f"{MAX_CACHEABLE_DEPARTMENTS}; the loader must degrade, not materialise"
    )


async def test_tenant_at_the_threshold_is_still_cacheable():
    """Boundary: exactly the threshold is fine — the guard is `>`, not `>=`."""
    at_limit = [
        _dept(i, 1 if i > 1 else None, f"/1/{i}/", is_department=1, tenant_id=1)
        for i in range(1, MAX_CACHEABLE_DEPARTMENTS + 1)
    ]
    with patch(
        "bisheng.permission.domain.services.department_org_domain._load_departments",
        new=AsyncMock(return_value=at_limit),
    ):
        mapping = await get_department_mapping(1)
    assert mapping is not None
    assert len(mapping) == MAX_CACHEABLE_DEPARTMENTS


# ---------------------------------------------------------------------------
# 3. Fail-closed
# ---------------------------------------------------------------------------


async def test_loader_failure_returns_none_not_empty_mapping():
    """A DB error must not masquerade as "no departments".

    An empty mapping is indistinguishable, to `nearest_org_path`, from a tenant
    whose org tree is genuinely empty — and both would deny. But the caller needs
    to tell "cannot evaluate" from "evaluated to nothing" so it can choose its
    fallback; conflating them hides outages behind 403s.
    """
    with patch(
        "bisheng.permission.domain.services.department_org_domain._load_departments",
        new=AsyncMock(side_effect=RuntimeError("db is down")),
    ):
        mapping = await get_department_mapping(1)
    assert mapping is None


async def test_uncached_fallback_agrees_with_the_cached_path_on_archived_nodes():
    """The two paths must give the SAME answer when an ancestor is archived.

    `get_department_mapping` reads through `aget_active_by_tenant`, which filters
    `status == 'active'` — so an ARCHIVED node is simply absent from the mapping,
    and `nearest_org_path` hits a dangling parent and returns None (deny).

    The oversized-tenant fallback reads through `aget_by_id`, which does NOT
    filter status. If it keeps walking through the archived node it returns that
    node's path — i.e. it GRANTS where the cached path DENIES.

    Two paths implementing one rule must not disagree, and the disagreement must
    not point in the permissive direction: a tenant large enough to skip the
    cache would silently get looser visibility than a small one, for the same
    data. AC-19 requires one visible set regardless of how it was resolved.
    """
    from bisheng.database.models.department import DepartmentDao
    from bisheng.permission.domain.services.permission_service import PermissionService

    archived_org = Department(
        id=4,
        dept_id="BS@archived-org",
        name="一级分行",
        parent_id=1,
        path="/1/4/",
        tenant_id=1,
        status="archived",  # <-- the whole point
        is_department=0,
    )
    child_dept = Department(
        id=6,
        dept_id="BS@child",
        name="信贷审批部2",
        parent_id=4,
        path="/1/4/6/",
        tenant_id=1,
        status="active",
        is_department=1,
    )

    async def _by_id(dept_id: int):
        return {4: archived_org, 6: child_dept}.get(int(dept_id))

    with patch.object(DepartmentDao, "aget_by_id", new=AsyncMock(side_effect=_by_id)):
        uncached = await PermissionService._resolve_org_domain_path_uncached(6)

    # The cached path sees no node 4 at all, so it cannot produce a domain.
    assert nearest_org_path(6, {6: (4, "/1/4/6/", 1)}) is None, "cached path denies"

    assert uncached is None, (
        "the uncached fallback returned a domain for a subtree whose 机构 ancestor "
        "is archived — it would grant visibility the cached path denies"
    )


async def test_build_mapping_from_rows_is_pure_and_shape_correct():
    """`build_department_mapping` is the pure half — no IO, deterministic shape."""
    rows = [
        _dept(1, None, "/1/", is_department=0),
        _dept(6, 4, "/1/4/6/", is_department=1),
    ]
    mapping = build_department_mapping(rows)
    assert mapping == {1: (None, "/1/", 0), 6: (4, "/1/4/6/", 1)}


async def test_rows_without_an_id_are_skipped_not_fatal():
    """A malformed row must not take the whole mapping down.

    Skipping is safe: a node missing from the mapping is unresolvable, and
    `nearest_org_path` fails closed on it.
    """
    rows = [_dept(1, None, "/1/", is_department=0), _dept(2, 1, "/1/2/", is_department=1)]
    rows[0].id = None
    mapping = build_department_mapping(rows)
    assert 1 not in mapping
    assert mapping == {2: (1, "/1/2/", 1)}
