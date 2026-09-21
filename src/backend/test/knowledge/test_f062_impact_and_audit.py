"""F062: the impact preview, and the audit trail for a marker flip / node move.

WHY AN IMPACT COUNT EXISTS AT ALL
---------------------------------
Flipping a node between 机构 and 部门 is not a metadata edit. It changes who can
see how many knowledge libraries, for everyone in and around that subtree. The
confirmation dialog's numbers are the operator's ONLY brake before an action
that is not undoable in effect (see INV-20: department-derived visibility has no
negative grant, so there is nothing to revoke).

That makes the count a security-relevant number, not a nicety, and it imposes
two requirements this file pins:

  1. **The count must be derived from the SAME rule as the actual visibility**
     (`department_org_domain`). A second, approximating implementation would
     let the dialog understate the blast radius — worse than no dialog, because
     the operator would believe a number that is wrong.
  2. **The number shown must equal the change that actually happens.** Asserting
     "> 0" would pass for any implementation; this file recomputes the expected
     difference from the tree and compares exactly.

WHY THE AUDIT TRAIL IS ASSERTED FIELD BY FIELD
----------------------------------------------
`audit_log.action` is a free-text column. A typo produces a row that is written,
accepted, and silently invisible to every query an auditor would run — the
failure mode the `TenantAuditAction` enum's docstring calls out. So the action
name is asserted against the enum, not against a literal typed in twice.

覆盖 AC: AC-05, AC-06, AC-07
"""

from unittest.mock import AsyncMock, patch

import pytest

from bisheng.department.domain.services.department_service import DepartmentService
from bisheng.permission.domain.services.department_org_domain import nearest_org_path

ORG = 0
DEPT = 1

# Same shape as the reference chart: a barrier inside a barrier.
#   1 总行/省联社 (机构)
#   ├── 2 信贷研发   (部门)
#   ├── 3 信贷审批部 (部门)
#   └── 4 一级分行   (机构)
#       ├── 5 信贷审批部1 (部门)
#       └── 6 信贷审批部2 (部门)
_ROWS = [
    (1, None, "/1/", ORG),
    (2, 1, "/1/2/", DEPT),
    (3, 1, "/1/3/", DEPT),
    (4, 1, "/1/4/", ORG),
    (5, 4, "/1/4/5/", DEPT),
    (6, 4, "/1/4/6/", DEPT),
]


def _mapping(overrides: dict[int, int] | None = None):
    rows = {r[0]: (r[1], r[2], r[3]) for r in _ROWS}
    for node_id, marker in (overrides or {}).items():
        parent, path, _old = rows[node_id]
        rows[node_id] = (parent, path, marker)
    return rows


def _visible_libraries(owning_dept_ids: list[int], user_dept_ids: list[int], mapping) -> set[int]:
    """The production rule, restated: a library is visible to a user whose path
    starts with the library's owning department's ORG DOMAIN."""
    out = set()
    for owning in owning_dept_ids:
        domain = nearest_org_path(owning, mapping)
        if domain is None:
            continue
        for user in user_dept_ids:
            user_path = mapping[user][1]
            if str(user_path).startswith(domain):
                out.add(owning)
                break
    return out


# ---------------------------------------------------------------------------
# AC-05 — the impact number matches what actually changes
# ---------------------------------------------------------------------------


class TestImpactIsExactNotApproximate:
    """Recompute the expected delta from the tree; do not accept "> 0"."""

    def test_flipping_a_barrier_opens_exactly_the_nested_departments(self):
        """4 (一级分行) is the barrier. Flipping it to 部门 merges its departments
        into 总行's domain, so a member of 信贷研发 gains exactly 4/5/6.

        Before the flip, 信贷研发 (2) already sees the WHOLE root domain minus
        一级分行's subtree: that is {1, 2, 3} — 信贷审批部 (3) is a sibling under
        the root, not behind the barrier. Getting this sanity line wrong is easy
        and would make the delta assertion meaningless, so it is spelled out.
        """
        libs = [1, 2, 3, 4, 5, 6]
        outsider = [2]  # a member of 信贷研发, outside 一级分行

        before = _visible_libraries(libs, outsider, _mapping())
        after = _visible_libraries(libs, outsider, _mapping({4: DEPT}))

        assert before == {1, 2, 3}, "sanity: the root's domain, excluding 一级分行"
        assert after == {1, 2, 3, 4, 5, 6}, "the barrier lifted; everything merges"
        assert after - before == {4, 5, 6}, "the impact is exactly these three"

    def test_flipping_either_way_has_the_same_magnitude(self):
        """Opening and closing touch the identical set of libraries.

        The magnitude is the SYMMETRIC DIFFERENCE between the two mappings'
        visible sets — which is the same set whichever direction you travel.
        Computed that way deliberately: subtracting in the order that happens to
        match the direction you are imagining yields an empty set for the
        narrowing case (V_closed ⊆ V_open), and an assertion that passes for
        the wrong reason.
        """
        libs = [1, 2, 3, 4, 5, 6]
        outsider = [2]
        v_open = _visible_libraries(libs, outsider, _mapping({4: DEPT}))
        v_closed = _visible_libraries(libs, outsider, _mapping())

        delta = v_open ^ v_closed
        assert delta == {4, 5, 6}
        assert v_open - v_closed == delta, "widening exposes exactly this set"
        assert v_closed - v_open == set(), "and narrowing exposes nothing new"

    def test_flipping_a_leaf_department_changes_nothing(self):
        """A node with no subtree below the barrier point has zero blast radius.

        Asserted because the count must be able to say ZERO — an implementation
        that always reports "something changed" makes the dialog noise and trains
        operators to click through it.
        """
        libs = [1, 2, 3, 4, 5, 6]
        outsider = [2]
        before = _visible_libraries(libs, outsider, _mapping())
        after = _visible_libraries(libs, outsider, _mapping({6: ORG}))
        assert before == after, "flipping a leaf 部门 to 机构 changes no outsider's view"


# ---------------------------------------------------------------------------
# AC-05 — the count is served from the SAME rule
# ---------------------------------------------------------------------------


async def test_impact_service_delegates_to_the_shared_lookup():
    """The service must DELEGATE to `nearest_org_path`, not re-derive the rule.

    That delegation is the property keeping the dialog honest: when the rule
    changes, the preview changes with it. Asserted by patching the shared lookup
    and requiring the service to call it — a second implementation would never
    touch this patch and the test would fail.

    The tenant mapping is stubbed so the function reaches the lookup without a
    database. A library IS supplied, deliberately: with none owned, the
    comparison loop never runs and the spy would read "not called" no matter how
    the rule were implemented — a vacuous assertion.
    """
    from bisheng.department.domain.services import department_service as mod
    from bisheng.permission.domain.services import department_org_domain

    mapping = _mapping()
    with (
        patch.object(
            department_org_domain,
            "get_department_mapping",
            new_callable=AsyncMock,
            return_value=mapping,
        ),
        patch.object(
            department_org_domain,
            "nearest_org_path",
            side_effect=department_org_domain.nearest_org_path,
        ) as spy,
        # One library owned by 一级分行 (4) — inside the node being flipped.
        patch.object(
            mod.DepartmentService,
            "_aget_owned_library_ids",
            new_callable=AsyncMock,
            return_value=[(100, 4)],
        ),
    ):
        result = await mod.DepartmentService._compute_node_type_impact(4)

    # Flipping 4 (机构 → 部门) moves library 100's domain from /1/4/ to /1/, which
    # newly exposes it to 4, 5 and 6.
    assert result == {"affected_departments": 3, "affected_libraries": 1}
    assert spy.called, "the impact must be computed WITH the shared rule, not beside it"


# ---------------------------------------------------------------------------
# AC-06 / AC-07 — audit trail
# ---------------------------------------------------------------------------


class _User:
    def __init__(self, user_id=1, user_role=None, tenant_id=1):
        self.user_id = user_id
        self.user_role = user_role if user_role is not None else [1]
        self.tenant_id = tenant_id
        self.is_global_super = False


SYS_ADMIN = _User(user_id=1, user_role=[1])


async def test_marker_flip_writes_a_structured_audit_row():
    """Field-by-field, because `action` is a free-text column.

    A typo there writes a row no auditor query will ever match — the exact
    failure the TenantAuditAction enum exists to prevent. So the expected action
    comes FROM the enum rather than being retyped here.
    """
    from bisheng.department.domain.constants import DepartmentAuditAction

    with (
        patch(
            "bisheng.database.models.audit_log.AuditLogDao.ainsert_v2",
            new_callable=AsyncMock,
        ) as audit,
        # The impact is computed INSIDE the audit helper — deliberately, so that
        # a failure to compute it is covered by the same best-effort guard as the
        # insert. Stub it here or this reaches for a real database.
        patch.object(
            DepartmentService,
            "_compute_node_type_impact",
            new_callable=AsyncMock,
            return_value={"affected_departments": 4, "affected_libraries": 2},
        ),
    ):
        await DepartmentService._audit_node_type_change(
            login_user=SYS_ADMIN,
            dept_id="BS@branch",
            from_is_department=0,
            to_is_department=1,
            dept_internal_id=4,
            action=DepartmentAuditAction.NODE_TYPE_CHANGED,
        )

    audit.assert_awaited_once()
    kw = audit.await_args.kwargs
    assert kw["action"] == DepartmentAuditAction.NODE_TYPE_CHANGED.value
    assert kw["action"] == "dept.node_type_changed"
    assert kw["target_type"] == "department"
    assert kw["target_id"] == "BS@branch"
    assert kw["operator_id"] == SYS_ADMIN.user_id
    meta = kw["metadata"]
    assert meta["from_is_department"] == 0
    assert meta["to_is_department"] == 1
    assert meta["affected_departments"] == 4
    assert meta["affected_libraries"] == 2


async def test_node_move_writes_a_structured_audit_row_with_both_parents():
    from bisheng.department.domain.constants import DepartmentAuditAction

    with (
        patch(
            "bisheng.database.models.audit_log.AuditLogDao.ainsert_v2",
            new_callable=AsyncMock,
        ) as audit,
        patch.object(
            DepartmentService,
            "_compute_node_type_impact",
            new_callable=AsyncMock,
            return_value={"affected_departments": 1, "affected_libraries": 1},
        ),
    ):
        await DepartmentService._audit_node_type_change(
            login_user=SYS_ADMIN,
            dept_id="BS@branch",
            from_is_department=0,
            to_is_department=0,
            dept_internal_id=4,
            action=DepartmentAuditAction.NODE_MOVED,
            extra={"from_parent_id": 1, "to_parent_id": 7},
        )

    kw = audit.await_args.kwargs
    assert kw["action"] == "dept.node_moved"
    assert kw["metadata"]["from_parent_id"] == 1
    assert kw["metadata"]["to_parent_id"] == 7


async def test_audit_failure_does_not_break_the_change():
    """Auditing must not be able to roll back a change the caller committed.

    Mirrors `_clear_knowledge_ownership`: a missed audit row is recoverable
    out-of-band, a half-applied department edit is not.
    """
    with (
        patch(
            "bisheng.database.models.audit_log.AuditLogDao.ainsert_v2",
            new_callable=AsyncMock,
            side_effect=RuntimeError("audit db down"),
        ),
        patch.object(
            DepartmentService,
            "_compute_node_type_impact",
            new_callable=AsyncMock,
            return_value={"affected_departments": 0, "affected_libraries": 0},
        ),
    ):
        # Must not raise.
        await DepartmentService._audit_node_type_change(
            login_user=SYS_ADMIN,
            dept_id="BS@branch",
            from_is_department=0,
            to_is_department=1,
            dept_internal_id=4,
            action="dept.node_type_changed",
        )


async def test_impact_computation_failure_also_does_not_break_the_change():
    """The audit's INPUT is inside the best-effort guard, not just its insert.

    This is the regression for the real defect: the impact used to be computed in
    an ARGUMENT position at the call site, so a failure there escaped after the
    caller had already committed — the endpoint would 500 on a change that had
    landed, and in `amove_department` it would skip the FGA tuple write and the
    tenant sync that follow. Computation failure must degrade the RECORD, not the
    change.
    """
    with (
        patch.object(
            DepartmentService,
            "_compute_node_type_impact",
            new_callable=AsyncMock,
            side_effect=RuntimeError("cannot evaluate"),
        ),
        patch(
            "bisheng.database.models.audit_log.AuditLogDao.ainsert_v2",
            new_callable=AsyncMock,
        ) as audit,
    ):
        await DepartmentService._audit_node_type_change(
            login_user=SYS_ADMIN,
            dept_id="BS@branch",
            from_is_department=0,
            to_is_department=1,
            dept_internal_id=4,
            action="dept.node_type_changed",
        )

    meta = audit.await_args.kwargs["metadata"]
    assert meta["impact_unavailable"] is True, "an unknown count must be recorded AS unknown"
    assert meta["affected_departments"] is None, (
        "writing 0 here would tell an auditor the change was measured and touched "
        "nothing — a different and false claim"
    )
