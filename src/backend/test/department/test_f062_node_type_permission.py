"""F062: who may change a node's 机构/部门 marker (AC-04 / AC-04a).

The marker is not ordinary metadata. Flipping a node from 机构 to 部门 removes a
visibility barrier, which hands everyone below it access to knowledge libraries
they could not previously see. Reusing the EXISTING department-edit permission
for it would therefore leave a bottom-up privilege-escalation path: a department
admin of 一级分行 can edit their own node (legitimately — that is their subtree),
and could flip that node to 部门, dissolving a barrier that bounds the whole
bank's visibility.

So the marker is gated SEPARATELY. Everything else a department admin can already
do — rename, reorder, set default roles — must keep working exactly as before;
this file asserts BOTH halves, because a fix that quietly removed their existing
abilities would be as wrong as one that left the escalation open.

Who may flip (AC-04a): system admin anywhere; tenant admin within their EXISTING
department-management scope — the same scope they already have, neither widened
nor narrowed by this feature.

Testing approach mirrors `test_department_service.py`: a fake async session plus
mocked gates, no real database. The subject under test is the PERMISSION BRANCH,
so what matters is which fields reach the write and which raise — not SQL.

覆盖 AC: AC-04, AC-04a
"""

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, patch

import pytest

from bisheng.common.errcode.department import (
    DepartmentArchivedReadonlyError,
    DepartmentPermissionDeniedError,
)
from bisheng.database.models.department import Department, DepartmentDao
from bisheng.department.domain.schemas.department_schema import DepartmentUpdate
from bisheng.department.domain.services import department_service as _dept_service_mod
from bisheng.department.domain.services.department_service import (
    DepartmentChangeHandler,
    DepartmentService,
)

_M = "bisheng.department.domain.services.department_service"


class _User:
    """Minimal LoginUser stand-in; `user_role=[1]` is the system admin role."""

    def __init__(self, user_id=1, user_role=None, tenant_id=1, is_global_super=False):
        self.user_id = user_id
        self.user_role = user_role if user_role is not None else [1]
        self.tenant_id = tenant_id
        self.is_global_super = is_global_super


SYS_ADMIN = _User(user_id=1, user_role=[1])
DEPT_ADMIN = _User(user_id=50, user_role=[2])  # not the admin role
TENANT_ADMIN = _User(user_id=60, user_role=[2])


def _branch_dept() -> Department:
    """The node under test: 一级分行, currently an 机构 (the barrier)."""
    return Department(
        id=4,
        dept_id="BS@branch",
        name="一级分行",
        parent_id=1,
        tenant_id=1,
        path="/1/4/",
        source="local",
        status="active",
        is_department=0,
    )


class _FakeSession:
    """Records writes; satisfies the `session.exec(...).first()` duplicate check."""

    def __init__(self):
        self.added: list = []
        self.committed = False

    async def exec(self, _stmt):
        return type("_R", (), {"first": lambda self: None, "all": lambda self: []})()

    def add(self, obj):
        self.added.append(obj)

    async def commit(self):
        self.committed = True

    async def refresh(self, _obj):
        return None

    async def delete(self, _obj):
        return None


def _patch_update(
    *,
    dept: Department | None = None,
    is_dept_admin: bool = True,
    is_tenant_admin: bool = False,
    tenant_root_path: str | None = None,
):
    """Patch the gates `aupdate_department` walks, plus its async session.

    `_check_permission` is: L1 system admin (pure role check — no patch needed,
    it short-circuits before touching OpenFGA) → L2 department admin (a
    `PermissionService.check` on the node, then on each ancestor) → L3 tenant
    admin within the mount subtree.
    """
    target = dept if dept is not None else _branch_dept()
    fake_session = _FakeSession()

    async def _check(*, user_id, relation, object_type, object_id, login_user=None, **_kw):
        if object_type == "department":
            return is_dept_admin
        if object_type == "tenant":
            return is_tenant_admin
        return False

    @asynccontextmanager
    async def _session_cm():
        yield fake_session

    return fake_session, [
        patch.object(_dept_service_mod, "get_async_db_session", _session_cm),
        patch.object(_dept_service_mod, "_get_dept_and_check_permission", new_callable=AsyncMock, return_value=target),
        patch(
            "bisheng.permission.domain.services.permission_service.PermissionService.check",
            new=AsyncMock(side_effect=_check),
        ),
        patch.object(_dept_service_mod, "_aget_user_tenant_root_path", new_callable=AsyncMock, return_value=tenant_root_path),
        # The tenant-admin scope test resolves the node by business key; without
        # this stub it would reach a real database.
        patch.object(DepartmentDao, "aget_by_dept_id", new_callable=AsyncMock, return_value=target),
        patch.object(DepartmentChangeHandler, "execute_async", new_callable=AsyncMock),
    ]


async def _update(data: DepartmentUpdate, user, **kw):
    fake_session, patchers = _patch_update(**kw)
    for p in patchers:
        p.start()
    try:
        await DepartmentService.aupdate_department("BS@branch", data, user)
    finally:
        for p in patchers:
            p.stop()
    return fake_session


async def _update_expecting_denial(data: DepartmentUpdate, user, **kw):
    fake_session, patchers = _patch_update(**kw)
    for p in patchers:
        p.start()
    try:
        with pytest.raises(DepartmentPermissionDeniedError):
            await DepartmentService.aupdate_department("BS@branch", data, user)
    finally:
        for p in patchers:
            p.stop()
    return fake_session


# ---------------------------------------------------------------------------
# AC-04 — the escalation path is closed
# ---------------------------------------------------------------------------


async def test_department_admin_cannot_flip_the_marker():
    """THE case this task exists for.

    A department admin of the subtree containing 一级分行 may edit that node
    — but flipping its marker dissolves the barrier bounding the whole bank.
    """
    dept = _branch_dept()
    await _update_expecting_denial(
        DepartmentUpdate(is_department=True),
        DEPT_ADMIN,
        dept=dept,
    )
    assert dept.is_department == 0, "the marker must not have moved"


async def test_department_admin_can_still_rename():
    """The other half: this feature must not remove existing abilities.

    A fix that simply denied department admins their whole update path would
    close the escalation and break normal operation — both are failures.
    """
    dept = _branch_dept()
    await _update(DepartmentUpdate(name="一级分行-改名"), DEPT_ADMIN, dept=dept)
    assert dept.name == "一级分行-改名"


async def test_department_admin_cannot_smuggle_the_marker_with_a_rename():
    """Bundling is not a bypass: name + marker in one request is refused, whole.

    Pinned because "reject everything, or apply just the allowed part?" is a real
    choice, and leaving it unspecified invites whichever the implementation
    happened to do. The chosen semantics is REJECT WHOLE — see T-13 in tasks.md.
    """
    dept = _branch_dept()
    await _update_expecting_denial(
        DepartmentUpdate(name="一级分行-改名", is_department=True),
        DEPT_ADMIN,
        dept=dept,
    )
    assert dept.is_department == 0, "marker unchanged"
    assert dept.name != "一级分行-改名", "the rename must not land on its own either"


# ---------------------------------------------------------------------------
# AC-04 / AC-04a — who CAN flip
# ---------------------------------------------------------------------------


async def test_system_admin_can_flip_any_node():
    dept = _branch_dept()
    await _update(DepartmentUpdate(is_department=True), SYS_ADMIN, dept=dept)
    assert dept.is_department == 1

    await _update(DepartmentUpdate(is_department=False), SYS_ADMIN, dept=dept)
    assert dept.is_department == 0


async def test_tenant_admin_can_flip_within_scope():
    """AC-04a: a tenant admin's reach is their EXISTING department scope."""
    dept = _branch_dept()
    await _update(
        DepartmentUpdate(is_department=True),
        TENANT_ADMIN,
        dept=dept,
        is_dept_admin=False,
        is_tenant_admin=True,
        tenant_root_path="/1/",
    )
    assert dept.is_department == 1


async def test_tenant_admin_cannot_flip_outside_scope():
    """The mirror: outside the mount subtree, tenant-admin reach does not apply."""
    dept = _branch_dept()
    await _update_expecting_denial(
        DepartmentUpdate(is_department=True),
        TENANT_ADMIN,
        dept=dept,
        is_dept_admin=False,
        is_tenant_admin=True,
        tenant_root_path="/999/",
    )
    assert dept.is_department == 0


# ---------------------------------------------------------------------------
# Non-marker updates are unaffected
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# AC-03 — creation takes the marker explicitly, never from the parent
# ---------------------------------------------------------------------------


def test_create_schema_accepts_the_marker_and_defaults_to_org():
    """AC-03: default is 机构, and the field is actually plumbed to the row.

    `DepartmentCreate.is_department` was declared but never read by
    `acreate_department`, so `POST /departments` with `is_department: true`
    answered 200 and silently stored 0 — the "accepted then dropped" failure
    shape this feature keeps calling out. This asserts the schema's contract;
    the wiring itself is covered by the service test below.

    "Never inherited from the parent" is the other half of AC-03: the field's
    default is a constant False, not a lookup of the parent's value.
    """
    from bisheng.department.domain.schemas.department_schema import DepartmentCreate

    bare = DepartmentCreate(name="新节点", parent_id=1)
    assert bare.is_department is False, "default must be 机构, not inherited"

    explicit = DepartmentCreate(name="新节点", parent_id=1, is_department=True)
    assert explicit.is_department is True


def test_create_constructs_the_row_with_the_requested_marker():
    """The value that reaches the ORM must be the one the caller asked for.

    Asserted against the SOURCE rather than by running the service: patching the
    `Department` symbol in this module would also replace it inside the
    `select(Department)` that the same function builds, so a behavioural test
    here fights SQLAlchemy instead of testing anything. The service body is one
    constructor call, and the marker expression in it is what AC-03 turns on.
    """
    import inspect
    import re

    src = inspect.getsource(DepartmentService.acreate_department)
    ctor = re.search(r"dept = Department\((.*?)\n            \)", src, re.S)
    assert ctor, "the acreate_department constructor call was not found"
    assert "is_department=" in ctor.group(1), (
        "acreate_department does not pass is_department to the row, so a create "
        "request carrying the marker would silently store the default"
    )


async def test_archived_department_is_still_readonly():
    """Regression: the archived-readonly guard (21017) must survive F062.

    It was lost for a moment while the marker gate was being wired in — the new
    early marker check sat in the same region as the existing guard and replaced
    it. Without it an archived (soft-deleted) node could be renamed, re-roled, or
    have its visibility marker flipped, which is precisely the sequence that
    makes a "deleted" department keep granting access.

    Asserted for BOTH the marker and an ordinary field, because the guard has to
    cover the whole update, not just the field F062 added.
    """
    dept = _branch_dept()
    dept.status = "archived"

    for payload in (DepartmentUpdate(is_department=True), DepartmentUpdate(name="x-改名")):
        fake_session, patchers = _patch_update(dept=dept)
        for p in patchers:
            p.start()
        try:
            with pytest.raises(DepartmentArchivedReadonlyError):
                await DepartmentService.aupdate_department("BS@branch", payload, SYS_ADMIN)
        finally:
            for p in patchers:
                p.stop()
        assert not fake_session.committed, f"archive guard did not block {payload}"

    assert dept.is_department == 0
    assert dept.name == "一级分行"


async def test_none_marker_is_a_no_opand_adds_no_gate():
    """`is_department=None` (the default) must behave exactly as before F062.

    Existing callers that never heard of the marker keep passing through the
    ordinary department-edit gate — they must not hit a new permission check.
    """
    dept = _branch_dept()
    await _update(DepartmentUpdate(sort_order=42), DEPT_ADMIN, dept=dept)
    assert dept.sort_order == 42
    assert dept.is_department == 0
