"""F062: the org-domain visibility rule, as a truth table.

The rule, from spec §2.2 (the single source of truth):

    D is visible to U  <=>  nearest_org_path(D) is a prefix of some U's path

`test_f062_nearest_org_path.py` pins the lookup. `test_f057_...` pins the wiring
into `PermissionService`. THIS file pins the RULE — the thing a reviewer will
argue about — by evaluating the same prefix test the production code evaluates,
against the org charts the requirement was written from.

Why the rule is restated here rather than driven through mocks: the cases below
ARE the acceptance criteria (AC-09..AC-20). If someone changes the production
lookup, this file must be re-derived from the spec, not from the code.
"""

import pytest

from bisheng.permission.domain.services.department_org_domain import nearest_org_path

ORG = 0
DEPT = 1

# ---------------------------------------------------------------------------
# The reference org chart — the one from the requirement, plus the shapes the
# review added (nested 机构, multi-layer 部门, an org as the LCA).
#
#   1  总行/省联社   (机构)
#   ├── 2  信贷研发        (部门)
#   │   └── 21 信贷研发一组 (部门)
#   │       └── 22 信贷研发一组A (部门)   <- multi-layer transparency
#   ├── 3  信贷审批部      (部门)
#   └── 4  一级分行        (机构)
#       ├── 5  信贷审批部1 (部门)
#       └── 6  信贷审批部2 (部门)   <- the user in the requirement sits here
#           └── 7  二级支行 (机构)   <- nested ORG: a barrier
#               └── 8  二级支行营业部 (部门)
# ---------------------------------------------------------------------------
_ROWS = [
    (1, None, "/1/", ORG),
    (2, 1, "/1/2/", DEPT),
    (21, 2, "/1/2/21/", DEPT),
    (22, 21, "/1/2/21/22/", DEPT),
    (3, 1, "/1/3/", DEPT),
    (4, 1, "/1/4/", ORG),
    (5, 4, "/1/4/5/", DEPT),
    (6, 4, "/1/4/6/", DEPT),
    (7, 6, "/1/6/7/", ORG),
    (8, 7, "/1/6/7/8/", DEPT),
]
TREE = {r[0]: (r[1], r[2], r[3]) for r in _ROWS}
ALL_ORG = {i: (p, path, ORG) for i, (p, path, _) in TREE.items()}


def visible(owning_dept_id: int, user_dept_id: int, tree=None) -> bool:
    """The production rule, restated: the domain path prefixes the user's path.

    Mirrors `PermissionService._implicit_department_library_level` after F062 —
    the left-hand side is the owning department's ORG DOMAIN, not its own path.
    """
    tree = TREE if tree is None else tree
    domain = nearest_org_path(owning_dept_id, tree)
    if domain is None:
        return False
    user_path = tree.get(user_dept_id, (None, None, None))[1]
    if not user_path:
        return False
    return str(user_path).startswith(domain)


def visible_multi(owning_dept_id: int, user_dept_ids: list[int], tree=None) -> bool:
    """AC-15: a user in several departments sees the UNION of their domains."""
    tree = TREE if tree is None else tree
    return any(visible(owning_dept_id, u, tree) for u in user_dept_ids)


# ---------------------------------------------------------------------------
# AC-09 / AC-10 / AC-11 — the requirement's own example
# ---------------------------------------------------------------------------


class TestRequirementExample:
    @pytest.mark.parametrize(
        "owning, label",
        [
            (6, "own department"),
            (5, "sibling department in the same org domain"),
            (4, "owning org itself"),
            (1, "root itself"),
            (2, "line under the root (信贷研发)"),
            (3, "line under the root (信贷审批部)"),
        ],
    )
    def test_all_six_named_nodes_are_visible_to_dept2(self, owning, label):
        assert visible(owning, 6), f"{label} must be visible to a member of 信贷审批部2"

    @pytest.mark.parametrize("owning", [5, 6, 4, 1, 2, 3, 21, 22])
    def test_org_domain_membership_is_symmetric(self, owning):
        """信贷审批部1 and 信贷审批部2 are peers — neither is privileged."""
        assert visible(owning, 5) == visible(owning, 6)

    def test_peer_departments_in_the_org_see_each_other(self):
        assert visible(5, 6) and visible(6, 5)


# ---------------------------------------------------------------------------
# AC-12 / AC-12a — nested 机构 is a barrier (B1)
# ---------------------------------------------------------------------------


class TestNestedOrgBarrier:
    def test_nested_org_itself_is_invisible(self):
        """AC-12: the nested 机构 node itself is not in the outer domain."""
        assert not visible(7, 6), "二级支行 must not leak into 一级分行's domain"

    def test_departments_under_a_nested_org_are_invisible(self):
        """AC-12a: nor is anything inside it."""
        assert not visible(8, 6), "二级支行营业部 sits behind the 二级支行 barrier"

    def test_two_sibling_orgs_do_not_see_each_other(self):
        """The distinguishing case from the design review (决策 5).

        The requirement's original example had ONE org under the root, so both
        readings ("org = barrier" and "org = a mere label") fit it. A second org
        under the root separates them: under the barrier reading, neither sees
        into the other.
        """
        tree = dict(TREE)
        tree[9] = (1, "/1/9/", ORG)  # 二级分行 — a peer org
        tree[10] = (9, "/1/9/10/", DEPT)  # its department

        assert not visible(9, 6, tree), "a peer 机构 is not visible"
        assert not visible(10, 6, tree), "nor is a peer 机构's department"

    def test_climbing_to_the_root_does_not_open_sibling_orgs(self):
        """Visibility upward is per-node, not "everything under the root"."""
        assert not visible(7, 5) and not visible(8, 5)


# ---------------------------------------------------------------------------
# AC-13 — departments are transparent at any depth
# ---------------------------------------------------------------------------


class TestDepartmentTransparency:
    def test_multi_layer_departments_resolve_to_the_top_org(self):
        assert nearest_org_path(21, TREE) == "/1/"
        assert nearest_org_path(22, TREE) == "/1/"

    def test_deep_department_is_visible_across_lines(self):
        """AC-13: 信贷研发一组A is visible to 信贷审批部 — a different line entirely."""
        assert visible(22, 3), "intermediate department depth must not matter"

    def test_inserting_a_department_layer_does_not_change_visibility(self):
        """The invariant that made 包含关系 win over 层级带 (决策 4).

        Wrapping 信贷研发 in one more department must not change who sees its
        libraries. Under a level-band rule this would flip.
        """
        before = visible(2, 6)
        tree = dict(TREE)
        tree[99] = (1, "/1/99/", DEPT)  # new layer between the root and 信贷研发
        tree[2] = (99, "/1/99/2/", DEPT)
        tree[21] = (2, "/1/99/2/21/", DEPT)
        tree[22] = (21, "/1/99/2/21/22/", DEPT)
        assert visible(2, 6, tree) == before
        assert before is True, "the case is only meaningful if it started visible"


# ---------------------------------------------------------------------------
# AC-11a / AC-14 — the LCA being an 机构 does not block
# ---------------------------------------------------------------------------


class TestLcaSemantics:
    def test_org_as_lca_does_not_block_cross_line_visibility(self):
        """AC-11a: 信贷研发 and 信贷审批部 share 总行 (an 机构) as LCA, and see each other.

        Reading the definition as "the path from the LCA to D contains an org"
        WITHOUT excluding the LCA itself would bar this, contradicting AC-14.
        This assertion pins the inclusive-vs-exclusive question.
        """
        assert visible(2, 3) and visible(3, 2)

    def test_root_marked_as_department_does_not_move_the_barrier(self):
        """AC-14: only INTERIOR nodes constrain domains; the root's marker is inert.

        The constraint doing the work here is 一级分行 (4), not the root — so
        flipping node 1 must not change any answer that depends on node 4.
        """
        tree = dict(TREE)
        tree[1] = (None, "/1/", DEPT)
        for owning in (4, 5, 6, 7, 8):
            for user in (4, 5, 6):
                assert visible(owning, user, tree) == visible(owning, user, TREE), (
                    f"root marker changed visibility of {owning}->{user}"
                )

    def test_nested_org_still_barriers_when_root_is_a_department(self):
        tree = dict(TREE)
        tree[1] = (None, "/1/", DEPT)
        assert not visible(7, 6, tree)
        assert not visible(8, 6, tree)


# ---------------------------------------------------------------------------
# AC-20 — all-机构 degenerates EXACTLY onto F057
# ---------------------------------------------------------------------------


class TestDegeneratesOntoF057:
    def test_all_orgs_is_identity_with_the_pre_f062_rule(self):
        """AC-20: with every node an 机构, the new rule == F057's rule.

        F057 compared the owning department's OWN path. Asserted here directly
        against the org-domain form — identical, not approximate. This is what
        makes the upgrade inert (AC-02).
        """
        checked = 0
        for owning in TREE:
            for user in TREE:
                f057 = str(TREE[user][1]).startswith(str(TREE[owning][1]))
                assert visible(owning, user, ALL_ORG) == f057, (
                    f"all-机构 must reproduce F057 for {owning}->{user}"
                )
                checked += 1
        assert checked == len(TREE) ** 2, "the sweep must cover every pair"

    def test_no_horizontal_visibility_when_all_orgs(self):
        """The横向 half disappears entirely; only ancestor→descendant remains."""
        assert visible(4, 6, ALL_ORG), "a descendant still sees its ancestor's library"
        assert not visible(5, 6, ALL_ORG), "siblings do NOT see each other"
        assert not visible(2, 3, ALL_ORG), "nor do distinct lines"


# ---------------------------------------------------------------------------
# AC-15 / AC-16 — membership shapes
# ---------------------------------------------------------------------------


class TestMembershipShape:
    def test_union_over_multiple_user_departments(self):
        """AC-15: membership in two places yields the union of both domains."""
        # 3 sits in the root domain; 6 sits in 一级分行's domain. 二级支行 (7)
        # and its department (8) are behind a barrier reachable from neither.
        assert visible_multi(3, [6]) is True
        assert visible_multi(2, [6]) is True
        assert visible_multi(7, [3, 6]) is False
        assert visible_multi(8, [3, 6]) is False

    def test_a_second_membership_widens_the_union(self):
        """Adding 8's own org to the user's memberships makes 8 visible."""
        assert visible(8, 8) is True, "a member of 8 sees 8's own library"
        assert visible_multi(8, [6, 8]) is True, "union picks it up via the 8 membership"

    def test_user_with_no_departments_sees_nothing(self):
        """AC-16: fail closed."""
        assert not any(visible(owning, -1) for owning in TREE)

    def test_library_with_no_owning_department_grants_nothing(self):
        assert visible(999, 6) is False


# ---------------------------------------------------------------------------
# AC-19 — tenant isolation
# ---------------------------------------------------------------------------


class TestTenantIsolation:
    def test_same_ids_different_tenants_do_not_cross(self):
        """AC-19: ids repeat across tenants; the mapping alone drives the answer."""
        tenant2 = {
            1: (None, "/1/", ORG),
            6: (1, "/1/6/", ORG),  # in tenant 2, node 6 is an ORG
        }
        assert nearest_org_path(6, TREE) == "/1/4/", "tenant 1: 6 is a 部门 under 4"
        assert nearest_org_path(6, tenant2) == "/1/6/", "tenant 2: 6 is an org itself"
