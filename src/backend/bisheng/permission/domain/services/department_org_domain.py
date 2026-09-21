"""F062: the organization-domain lookup that backs department-owned visibility.

WHAT THIS FILE IS FOR
---------------------
F057 made a department-owned library visible to that department and its
descendants. F062 extends that to an "org domain": everything inside the
nearest 机构 ancestor is mutually visible, while 机构 nodes are opaque barriers
that stop the spread. The whole rule reduces to:

    D is visible to U  <=>  nearest_org_path(D) is a prefix of some U's path

so this module owns exactly two things: the pure path lookup, and the tenant
mapping it needs. The permission rule itself stays in
`PermissionService._implicit_department_library_level`, which is where F057 put
it — only the left-hand side of its prefix test changes.

WHY THE MAPPING IS CACHED, AND WHY IT IS CACHED **PER TENANT**
--------------------------------------------------------------
Loading the tree per check would be ruinous: the list path runs the check per
row in 50-row batches, and `department_stats` runs it for every
department-owned library. So it is loaded once per tenant and cached.

The tenant dimension is not an optimisation, it is a correctness requirement.
Department ids are unique only WITHIN a tenant, so a cache keyed by dept id
alone would let tenant A's lookup resolve against tenant B's tree — a
cross-tenant permission grant, silent and indistinguishable from a legitimate
one. C3 ("Multi-Tenancy — Auto-Injected, Never Manual") is the law here.

WHY THERE IS A SIZE THRESHOLD
-----------------------------
The deployment this feature targets has ~500-1000 departments, which fits in
memory trivially. This repo has nevertheless seen 3w+ (`department_service.py`
:1518 documents super-admin tree loads materialising exactly that, and :1519
records DM8 bind-parameter serialisation as the bottleneck). Rather than assume
the small case, the loader refuses to materialise past
`MAX_CACHEABLE_DEPARTMENTS` and returns None; the caller then falls back to
walking `parent_id` one node at a time. That path is slower but correct, and it
serves deployments this feature does not target.

WHY `_load_departments` IS A SEPARATE, PATCHABLE FUNCTION
---------------------------------------------------------
It is the only IO in this module. Keeping it as a module-level function with a
single `tenant_id` argument gives tests a seam to substitute, and keeps the
purity of everything else obvious.

WHY `is_department` AND NOT `is_org`
------------------------------------
The value mapping is 0=机构 / 1=部门, so an `is_org` field would read
`is_org == 0` for "this IS an org" — an inverted boolean prefix. See the
migration `v2_7_0_f062_department_node_type.py`.
"""

from __future__ import annotations

import logging
import time
from bisheng.database.models.department import Department, DepartmentDao

logger = logging.getLogger(__name__)

#: A tenant whose org tree exceeds this is NOT materialised into a cached
#: mapping; callers fall back to per-node traversal. Sized at ~5x the target
#: deployment (500-1000 nodes) so the guard never fires there, while still
#: protecting the 3w+-department case documented in `department_service.py:1518`.
MAX_CACHEABLE_DEPARTMENTS = 5000

#: How long a loaded mapping stays usable. Deliberately short and in the same
#: order as `PermissionCache.TTL` (10s): department-tree edits are rare, and a
#: stale mapping mis-resolves visibility in whichever direction the edit went.
#: Every department-mutating path also invalidates explicitly (see
#: `invalidate_department_mapping`), so this is the backstop, not the mechanism.
_CACHE_TTL_SECONDS = 10.0

#: tenant_id -> (loaded_at_monotonic, mapping). Process-local on purpose: the
#: mapping is small, read on a hot path, and rebuilding it costs one query.
_cache: dict[int, tuple[float, dict[int, tuple[int | None, str, int]]]] = {}

#: How far up the chain a single lookup may walk before giving up. A cycle can
#: outlive the code that created it (a half-applied reparent), so the walk is
#: bounded rather than trusting the tree to be a tree.
_MAX_WALK_STEPS = 256


def build_department_mapping(rows) -> dict[int, tuple[int | None, str, int]]:
    """Build `{id: (parent_id, path, is_department)}` from Department rows.

    Pure. Rows without an id are skipped rather than raising: a node absent from
    the mapping is unresolvable, and `nearest_org_path` fails closed on it, so
    one malformed row cannot take the whole tenant's visibility down.
    """
    mapping: dict[int, tuple[int | None, str, int]] = {}
    for row in rows:
        row_id = getattr(row, "id", None)
        if row_id is None:
            continue
        mapping[int(row_id)] = (
            getattr(row, "parent_id", None),
            str(getattr(row, "path", "") or ""),
            int(getattr(row, "is_department", 0) or 0),
        )
    return mapping


async def _load_departments(tenant_id: int) -> list[Department]:
    """The module's only IO. Separated so tests can substitute it."""
    return await DepartmentDao.aget_active_by_tenant(tenant_id)


def _current_tenant_id() -> int:
    from bisheng.core.context.tenant import get_current_tenant_id

    return get_current_tenant_id() or 1


async def get_department_mapping(tenant_id: int | None = None) -> dict[int, tuple[int | None, str, int]] | None:
    """Return the tenant's org-tree mapping, or None when it cannot be served.

    None is a MEANINGFUL distinct result from `{}`:
      - `{}`  -> "this tenant genuinely has no departments" (a valid answer)
      - None  -> "cannot answer" (oversized, or the load failed)
    The caller must fall back on None, and must never treat it as "no orgs".
    """
    if tenant_id is None:
        tenant_id = _current_tenant_id()
    tenant_id = int(tenant_id)

    cached = _cache.get(tenant_id)
    if cached is not None:
        loaded_at, mapping = cached
        if time.monotonic() - loaded_at < _CACHE_TTL_SECONDS:
            return mapping
        _cache.pop(tenant_id, None)

    try:
        rows = await _load_departments(tenant_id)
    except Exception as e:
        # Fail closed: None (not {}), so the caller does not read an outage as
        # "this tenant has no departments" and deny against a wrong path.
        logger.warning("F062: could not load department mapping for tenant %s: %s", tenant_id, e)
        return None

    if len(rows) > MAX_CACHEABLE_DEPARTMENTS:
        logger.warning(
            "F062: tenant %s has %d departments (> %d); skipping mapping cache and "
            "falling back to per-node traversal",
            tenant_id,
            len(rows),
            MAX_CACHEABLE_DEPARTMENTS,
        )
        return None

    mapping = build_department_mapping(rows)
    _cache[tenant_id] = (time.monotonic(), mapping)
    return mapping


def clear_department_mapping_cache() -> None:
    """Drop every cached mapping. Used by tests and by explicit invalidation."""
    _cache.clear()


def invalidate_department_mapping(tenant_id: int | None = None) -> None:
    """Drop the cached mapping for one tenant (or all, when omitted).

    Called from every path that mutates the org tree — create / rename / move /
    archive / node-type flip. A stale mapping mis-resolves visibility in
    whichever direction the edit went, so this is a correctness call, not a
    performance one.
    """
    if tenant_id is None:
        _cache.clear()
        return
    _cache.pop(int(tenant_id), None)


def nearest_org_path(
    dept_id: int,
    mapping: dict[int, tuple[int | None, str, int]],
) -> str | None:
    """The path of `dept_id`'s org domain — its own path when it IS an org.

    SELF FIRST, THEN ANCESTORS. That ordering is the entire nested-barrier rule:
    an 机构's nearest org is itself, so it never falls into its parent org's
    domain. Written the other way round (strict ancestors only) the barrier
    silently disappears one level down and the feature degrades to "everything
    inside the root is mutually visible".

    Returns None — never a path — whenever the chain cannot be resolved: unknown
    id, dangling parent, or a cycle. Callers treat None as "no org-domain grant",
    which is the fail-closed direction.
    """
    current = int(dept_id)
    steps = 0
    while steps < _MAX_WALK_STEPS:
        entry = mapping.get(current)
        if entry is None:
            return None
        parent_id, path, is_department = entry
        if not is_department:
            # An 机构 (including this node itself) terminates the domain.
            return path or None
        if parent_id is None:
            # Walked off the top without meeting an org. The requirement makes
            # the root's type immaterial (AC-14); this node simply has no org
            # above it, so there is no domain to grant from.
            return None
        current = int(parent_id)
        steps += 1

    logger.warning("F062: department chain from %s exceeded %d steps (cycle?); failing closed", dept_id, _MAX_WALK_STEPS)
    return None
