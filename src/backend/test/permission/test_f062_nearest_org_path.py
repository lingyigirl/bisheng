"""F062: `nearest_org_path` — the org-domain lookup, as a pure function.

The whole visibility rule reduces to one prefix test:

    D is visible to U  <=>  nearest_org_path(D)  is a prefix of  some U's path

where `nearest_org_path(D)` is D's own path when D is an 机构 (opaque, the
default), else the path of D's NEAREST 机构 ancestor (walking up `parent_id`).

This file pins the pure function in isolation: it takes a pre-built mapping and
must not touch the database, the cache, or the tenant context. That purity is
what lets `permission/` call it without acquiring a dependency on the
`department` domain.

The single easiest mistake this file exists to catch: taking the nearest 机构
ANCESTOR (strict) instead of "self-if-org, else nearest ancestor". The strict
reading lets a nested 机构 be absorbed into its parent's domain, which silently
reverts the feature to "no barriers at all" one level down. `test_self_is_org_*`
below fails on that mistake; it is the canary, not a formality.

覆盖 AC: AC-09, AC-12, AC-12a, AC-13, AC-14
"""

from bisheng.permission.domain.services.department_org_domain import nearest_org_path


def _tree(*rows):
    """Build the `{dept_id: (parent_id, path, is_department)}` mapping under test.

    `rows` are `(id, parent_id, path, is_department)`; path is the production
    materialized form `/1/2/3/` whose LAST segment is the node's own id.
    """
    return {r[0]: (r[1], r[2], r[3]) for r in rows}


# ---------------------------------------------------------------------------
# The tree used across these cases (matches the org chart this feature targets)
#
#   1  总行/省联社   (机构)
#   ├── 2  信贷研发        (部门)
#   │   └── 21 信贷研发一组 (部门)   <- nested DEPARTMENTS, must be transparent
#   ├── 3  信贷审批部      (部门)
#   └── 4  一级分行        (机构)
#       ├── 5  信贷审批部1 (部门)
#       └── 6  信贷审批部2 (部门)   <- the user sits here
#           └── 7  二级支行 (机构)   <- nested ORG, must be a barrier
#               └── 8  二级支行营业部 (部门)
# ---------------------------------------------------------------------------
ORG_TREE = _tree(
    (1, None, "/1/", 0),
    (2, 1, "/1/2/", 1),
    (21, 2, "/1/2/21/", 1),
    (3, 1, "/1/3/", 1),
    (4, 1, "/1/4/", 0),
    (5, 4, "/1/4/5/", 1),
    (6, 4, "/1/4/6/", 1),
    (7, 6, "/1/6/7/", 0),
    (8, 7, "/1/6/7/8/", 1),
)


# ---------------------------------------------------------------------------
# Self is an org -> return SELF, not the strict ancestor
# ---------------------------------------------------------------------------


def test_self_is_org_returns_own_path():
    """THE canary: an 机构's domain starts at itself, not one level up.

    A strict-ancestor implementation returns `/1/` here (总行 is 4's nearest org
    ancestor), which would fold 一级分行 into 总行's domain and erase the barrier
    between it and its siblings. This assertion is what catches that.
    """
    assert nearest_org_path(4, ORG_TREE) == "/1/4/"


def test_nested_org_returns_itself_not_its_parent_org():
    """二级支行 (7) is an org nested under 一级分行 (4).

    Its nearest org must be itself, so it stays excluded from 一级分行's domain.
    """
    assert nearest_org_path(7, ORG_TREE) == "/1/6/7/"


def test_root_is_org_returns_root_path():
    assert nearest_org_path(1, ORG_TREE) == "/1/"


# ---------------------------------------------------------------------------
# Self is a department -> nearest org ancestor
# ---------------------------------------------------------------------------


def test_department_under_org_returns_that_org():
    assert nearest_org_path(5, ORG_TREE) == "/1/4/"
    assert nearest_org_path(6, ORG_TREE) == "/1/4/"


def test_department_at_root_layer_returns_root():
    """信贷研发 (2) and 信贷审批部 (3) hang directly off 总行 (1)."""
    assert nearest_org_path(2, ORG_TREE) == "/1/"
    assert nearest_org_path(3, ORG_TREE) == "/1/"


def test_department_under_department_takes_the_org_above_both():
    """Multi-layer departments are transparent: 一组 (21) -> 研发 (2) -> 总行 (1).

    The walk must keep going through 部门 nodes rather than stopping at the
    immediate parent.
    """
    assert nearest_org_path(21, ORG_TREE) == "/1/"


def test_department_inside_nested_org_takes_the_nested_org():
    """二层营业部 (8) sits under the nested 机构 7, so the barrier is 7 — not 1."""
    assert nearest_org_path(8, ORG_TREE) == "/1/6/7/"


def test_nearest_org_wins_over_a_further_one():
    """Picks the CLOSEST org, not the first/root one."""
    tree = _tree(
        (1, None, "/1/", 0),
        (4, 1, "/1/4/", 0),
        (9, 4, "/1/4/9/", 0),
        (10, 9, "/1/4/9/10/", 1),
    )
    assert nearest_org_path(10, tree) == "/1/4/9/"


# ---------------------------------------------------------------------------
# Fail-closed paths — every one of these must yield None (never a path)
# ---------------------------------------------------------------------------


def test_missing_id_returns_none():
    assert nearest_org_path(999, ORG_TREE) is None


def test_empty_mapping_returns_none():
    assert nearest_org_path(1, {}) is None


def test_dangling_parent_returns_none():
    """A node whose parent_id points at a row that is not in the mapping.

    Must terminate and fail closed — not loop, and not treat the missing parent
    as a root it may grant against.
    """
    tree = _tree((10, 999, "/1/999/10/", 1))
    assert nearest_org_path(10, tree) is None


def test_cycle_containing_an_org_stops_at_that_org():
    """A malformed cycle must not hang, and the org in it still terminates the walk.

    `10` is a department whose parent `11` is an 机构 whose parent is `10` again.
    The cycle is never traversed: the walk hits `11` on the first step and stops,
    because an org terminates its own domain. Asserting the PATH (not None) pins
    that the guard does not turn a resolvable node into a denial.
    """
    cyclic = {10: (11, "/1/10/", 1), 11: (10, "/1/11/", 0)}
    assert nearest_org_path(10, cyclic) == "/1/11/"


def test_all_department_cycle_terminates_and_returns_none():
    """A cycle with NO org in it must give up rather than spin forever.

    A cycle can outlive the code that created it (a half-applied reparent), and
    with every node a 部门 the walk would never meet a terminator. This is the
    case the step bound exists for.
    """
    cyclic = {10: (11, "/1/10/", 1), 11: (10, "/1/11/", 1)}
    assert nearest_org_path(10, cyclic) is None


def test_self_parent_department_terminates_and_returns_none():
    """The degenerate cycle: a department that is its own parent."""
    assert nearest_org_path(10, {10: (10, "/1/10/", 1)}) is None


# ---------------------------------------------------------------------------
# Path shape is returned verbatim (the caller does the prefix test)
# ---------------------------------------------------------------------------


def test_returns_the_path_verbatim_including_trailing_slash():
    """The trailing slash is the prefix test's payload.

    `/1/4/` must never be returned without it, or `/1/4/`-vs-`/1/40/` becomes a
    false ancestor match (the cross-department leak F057 pinned at
    test_f057_department_ownership_implicit.py).
    """
    p = nearest_org_path(6, ORG_TREE)
    assert p == "/1/4/"
    assert p.endswith("/")
