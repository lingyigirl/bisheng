"""F062 round 2: the move impact preview, and the all-or-nothing batch.

Both were added after the first review found the gaps, and both carry a
distinctive failure mode worth pinning:

* **Move impact** must describe the move that is ABOUT TO HAPPEN, not a marker
  flip (`_compute_move_impact`). The version it replaced logged the flip's
  numbers, i.e. a change that never occurred — a wrong number in an audit trail
  and (once the dialog used it) in the operator's confirmation.
* **Batch** must be all-or-nothing (`abatch_set_node_type`). Applying half a
  selection expresses one intention as several different facts about the tree,
  and the tree is what decides who can read what.

The pieces that are NOT here: the endpoint/HTTP layer and the confirm dialog are
covered by `test_e2e_f062_*` and the manual checklist respectively. This file
pins the computation and the transaction boundary.

覆盖 AC: AC-05, AC-08
"""

from unittest.mock import AsyncMock, patch

import pytest

from bisheng.department.domain.services.department_service import DepartmentService
from bisheng.permission.domain.services.department_org_domain import nearest_org_path

ORG = 0
DEPT = 1

#   1 总行 (机构)
#   ├── 2 研发 (部门)
#   └── 4 分行 (机构)
#       ├── 5 审批1 (部门)   <- user here
#       └── 6 审批2 (部门)
_ROWS = [
    (1, None, "/1/", ORG),
    (2, 1, "/1/2/", DEPT),
    (4, 1, "/1/4/", ORG),
    (5, 4, "/1/4/5/", DEPT),
    (6, 4, "/1/4/6/", DEPT),
]


def _mapping():
    return {r[0]: (r[1], r[2], r[3]) for r in _ROWS}


# ---------------------------------------------------------------------------
# AC-05 — the move impact mirrors what amove_department actually writes
# ---------------------------------------------------------------------------


class TestMoveHypotheticalTree:
    """The hypothetical tree must match the real write, or the number lies."""

    def _moved(self, node_id: int, new_parent_id: int):
        """Re-derive what `_compute_move_impact`'s closure builds, from a mapping.

        Kept as a local restatement so the assertions below describe the SHAPE
        the production closure must produce; `_compute_move_impact` is exercised
        end-to-end just underneath.
        """
        m = _mapping()
        entry = m[node_id]
        new_parent = m[new_parent_id]
        old_path = str(entry[1])
        new_path = f"{new_parent[1]}{node_id}/"
        out = dict(m)
        out[node_id] = (new_parent_id, new_path, entry[2])
        for nid, (parent_id, path, marker) in m.items():
            if nid == node_id:
                continue
            if str(path).startswith(old_path):
                out[nid] = (parent_id, new_path + str(path)[len(old_path):], marker)
        return out

    def test_moving_a_department_reparents_it_and_rebases_its_path(self):
        moved = self._moved(5, 6)  # 审批1 under 审批2
        assert moved[5] == (6, "/1/4/6/5/", DEPT)

    def test_moving_an_org_rebases_its_whole_subtree(self):
        """The subtree's paths must move with it — that is what `update_paths_batch`
        does, and what makes a move relocate every node's org domain."""
        moved = self._moved(4, 2)  # 分行 under 研发
        assert moved[4] == (2, "/1/2/4/", ORG)
        assert moved[5] == (4, "/1/2/4/5/", DEPT), "descendant rebased"
        assert moved[6] == (4, "/1/2/4/6/", DEPT), "sibling descendant rebased too"

    def test_moving_an_org_moves_the_barrier_with_it_not_removes_it(self):
        """The bar stays a bar — at its NEW path. Getting this wrong is easy.

        Moving 分行 under 研发 does NOT dissolve the barrier: 分行 is still an
        机构, so everything inside it still resolves to 分行 — just at
        `/1/2/4/` instead of `/1/4/`. The visibility change is therefore about
        WHO is now outside the barrier (研发 moved from outside to ancestor),
        not about the barrier going away.

        Asserted explicitly because "moving an org dissolves its barrier" is the
        intuitive-but-wrong reading, and it would make the preview report a
        change nobody is actually subject to.
        """
        before = nearest_org_path(5, _mapping())
        after = nearest_org_path(5, self._moved(4, 2))
        assert before == "/1/4/", "sanity: 审批1 sits behind the 分行 barrier"
        assert after == "/1/2/4/", "the barrier followed its node — it did not vanish"
        assert after != "/1/", "the domain must NOT collapse to the root's"
        assert before != after, "the move still changes visibility — it must not report zero"

    def test_a_move_that_does_not_change_any_domain_is_still_detected_as_zero(self):
        """Same-parent reorder: paths unchanged, so no domain moves."""
        moved = self._moved(5, 4)  # 审批1 already under 分行
        assert moved[5] == (4, "/1/4/5/", DEPT)
        assert nearest_org_path(5, moved) == nearest_org_path(5, _mapping())


# ---------------------------------------------------------------------------
# AC-05 — the impact service itself
# ---------------------------------------------------------------------------


async def test_move_impact_delegates_to_the_shared_rule():
    """Same contract as the flip's preview: use `nearest_org_path`, do not re-derive.

    The scenario is deliberately visibility-NEUTRAL: 分行 (4) moves under 研发
    (2), which relocates the barrier's path from `/1/4/` to `/1/2/4/` without
    changing which departments sit behind it. The domains move; nobody's view
    does. A move preview that reported a non-zero count here would be warning
    about a change no one experiences.

    The point of the assertion is the DELEGATION (spy called) plus the correct
    neutral answer — not the size of a delta.
    """
    from bisheng.department.domain.services import department_service as mod
    from bisheng.permission.domain.services import department_org_domain

    mapping = _mapping()
    with (
        patch.object(
            department_org_domain, "get_department_mapping", new_callable=AsyncMock, return_value=mapping
        ),
        patch.object(
            department_org_domain, "nearest_org_path", side_effect=department_org_domain.nearest_org_path
        ) as spy,
        patch.object(mod.DepartmentService, "_aget_owned_library_ids", new_callable=AsyncMock, return_value=[(100, 5)]),
    ):
        result = await mod.DepartmentService._compute_move_impact(4, 2)

    assert spy.called, "the move preview must be computed WITH the shared rule"
    assert result["affected_departments"] == 0, (
        "the barrier moved but nobody's view did — reporting a change here would "
        "send the operator through a confirmation dialog about nothing"
    )
    assert result["affected_libraries"] == 0


async def test_move_impact_is_not_the_flip_impact():
    """The regression for the defect this replaced.

    The old code logged the FLIP's numbers under a MOVE audit row. Asserting the
    two computations differ for the same node keeps them from being silently
    re-merged: flipping 分行 (机构 -> 部门) and moving 分行 under 研发 are
    different changes with different blast radii.
    """
    from bisheng.department.domain.services import department_service as mod
    from bisheng.permission.domain.services import department_org_domain

    mapping = _mapping()
    owned = [(100, 5), (101, 6), (102, 4)]
    with (
        patch.object(department_org_domain, "get_department_mapping", new_callable=AsyncMock, return_value=mapping),
        patch.object(mod.DepartmentService, "_aget_owned_library_ids", new_callable=AsyncMock, return_value=owned),
    ):
        flip = await mod.DepartmentService._compute_node_type_impact(4)
        move = await mod.DepartmentService._compute_move_impact(4, 2)

    assert (flip["affected_departments"], flip["affected_libraries"]) != (
        move["affected_departments"],
        move["affected_libraries"],
    ), "flip and move must not produce the same numbers — they are different changes"


async def test_move_impact_uses_the_viewers_path_AFTER_the_move_too():
    """Regression: both sides of the comparison must use their own tree's path.

    A move rewrites the path of every node in the moved subtree. If the "after"
    test reads the viewer's path from the OLD mapping, it compares a stale path
    against the new domain and flags a change for viewers whose view never moved
    — inflating the number the dialog shows and the audit records.

    Built so the move is visibility-neutral for the library's owner: 审批1 (5)
    moves between two siblings UNDER THE SAME 机构 (4), so its domain stays
    `/1/4/` and nobody's view changes. A path-blind implementation reports a
    non-zero count here.
    """
    from bisheng.department.domain.services import department_service as mod
    from bisheng.permission.domain.services import department_org_domain

    # Extend the reference tree with a second 部门 under 分行, to move 5 onto.
    mapping = _mapping()
    mapping[7] = (4, "/1/4/7/", DEPT)

    with (
        patch.object(department_org_domain, "get_department_mapping", new_callable=AsyncMock, return_value=mapping),
        # The library belongs to the moving node itself.
        patch.object(mod.DepartmentService, "_aget_owned_library_ids", new_callable=AsyncMock, return_value=[(100, 5)]),
    ):
        result = await mod.DepartmentService._compute_move_impact(5, 7)

    assert result["affected_departments"] == 0, (
        "moving a department between two siblings inside the SAME org domain "
        "changes nobody's view; a non-zero count means the comparison used a "
        "stale viewer path"
    )
    assert result["affected_libraries"] == 0


async def test_move_impact_fails_closed_without_a_mapping():
    from bisheng.department.domain.services import department_service as mod
    from bisheng.permission.domain.services import department_org_domain

    with patch.object(
        department_org_domain, "get_department_mapping", new_callable=AsyncMock, return_value=None
    ):
        result = await mod.DepartmentService._compute_move_impact(4, 2)
    assert result.get("unavailable") is True
    assert result["affected_departments"] == 0


async def test_move_impact_with_an_unknown_target_parent_does_not_raise():
    """A bogus new parent must degrade, not raise — this runs before a commit."""
    from bisheng.department.domain.services import department_service as mod
    from bisheng.permission.domain.services import department_org_domain

    with (
        patch.object(department_org_domain, "get_department_mapping", new_callable=AsyncMock, return_value=_mapping()),
        patch.object(mod.DepartmentService, "_aget_owned_library_ids", new_callable=AsyncMock, return_value=[]),
    ):
        result = await mod.DepartmentService._compute_move_impact(4, 9999)
    assert result["affected_departments"] == 0


# ---------------------------------------------------------------------------
# AC-08 — the batch is all-or-nothing
# ---------------------------------------------------------------------------


class _User:
    def __init__(self, user_id=1, user_role=None, tenant_id=1):
        self.user_id = user_id
        self.user_role = user_role if user_role is not None else [1]
        self.tenant_id = tenant_id
        self.is_global_super = False


SYS_ADMIN = _User(user_id=1, user_role=[1])


def _dept(dept_id: str, internal_id: int, status: str = "active", marker: int = ORG):
    from bisheng.database.models.department import Department

    return Department(
        id=internal_id,
        dept_id=dept_id,
        name=dept_id,
        parent_id=1,
        tenant_id=1,
        path=f"/1/{internal_id}/",
        source="local",
        status=status,
        is_department=marker,
    )


class _BatchPayload:
    def __init__(self, dept_ids, is_department):
        self.dept_ids = dept_ids
        self.is_department = is_department


async def test_batch_rejects_the_whole_selection_when_one_node_is_unauthorised():
    """THE property: one bad node must not leave the others annotated.

    A partial apply would express the operator's single intention ("these are
    departments") as two different facts about the tree, and it is the tree that
    decides who can read what.
    """
    from bisheng.common.errcode.department import DepartmentPermissionDeniedError
    from bisheng.database.models.department import DepartmentDao
    from bisheng.department.domain.services import department_service as mod

    rows = {"BS@a": _dept("BS@a", 10), "BS@b": _dept("BS@b", 11)}

    async def _by_id(dept_id):
        return rows.get(dept_id)

    authorised = {"BS@a"}  # BS@b is NOT

    async def _ensure(_login_user, dept_id):
        if dept_id not in authorised:
            raise DepartmentPermissionDeniedError()

    commits = []

    class _Session:
        async def exec(self, _stmt):
            return type("R", (), {"first": lambda self: None, "all": lambda self: []})()

        def add(self, obj):  # pragma: no cover - must never run
            commits.append(obj)

        async def commit(self):  # pragma: no cover - must never run
            commits.append("commit")

    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def _cm():
        yield _Session()

    with (
        patch.object(mod.DepartmentService, "_ensure_may_change_node_type", new=AsyncMock(side_effect=_ensure)),
        patch.object(DepartmentDao, "aget_by_dept_id", new=AsyncMock(side_effect=_by_id)),
        patch.object(mod, "get_async_db_session", _cm),
    ):
        with pytest.raises(DepartmentPermissionDeniedError):
            await mod.DepartmentService.abatch_set_node_type(
                _BatchPayload(["BS@a", "BS@b"], True), SYS_ADMIN
            )

    assert not commits, "nothing may be written when ANY node is refused"


async def test_batch_rejects_an_archived_node_before_writing_anything():
    from bisheng.common.errcode.department import DepartmentArchivedReadonlyError
    from bisheng.database.models.department import DepartmentDao
    from bisheng.department.domain.services import department_service as mod

    rows = {"BS@a": _dept("BS@a", 10), "BS@z": _dept("BS@z", 12, status="archived")}
    writes = []

    class _Session:
        async def exec(self, _stmt):
            return type("R", (), {"first": lambda self: None})()

        def add(self, obj):  # pragma: no cover
            writes.append(obj)

        async def commit(self):  # pragma: no cover
            writes.append("commit")

    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def _cm():
        yield _Session()

    with (
        patch.object(mod.DepartmentService, "_ensure_may_change_node_type", new=AsyncMock()),
        patch.object(DepartmentDao, "aget_by_dept_id", new=AsyncMock(side_effect=lambda i: rows.get(i))),
        patch.object(mod, "get_async_db_session", _cm),
    ):
        with pytest.raises(DepartmentArchivedReadonlyError):
            await mod.DepartmentService.abatch_set_node_type(_BatchPayload(["BS@a", "BS@z"], True), SYS_ADMIN)

    assert not writes


# ---------------------------------------------------------------------------
# NOT UNIT-TESTED HERE: the batch's write phase
#
# The happy path (`is_department` applied, idempotent nodes skipped, the audit's
# `from` reverse-derived) is deliberately left to the E2E suite rather than
# forced through mocks. Reproducing it here needs a session stub that satisfies
# `session.exec(...).first()` inside the write loop while a `DepartmentDao` mock
# serves the phase-1 reads — and a test that spends its assertions negotiating
# with `unittest.mock` stops testing the business logic. The two tests above pin
# what matters and CAN be pinned here: nothing is written when any node is
# refused, and nothing is written when any node is archived.
# ---------------------------------------------------------------------------
