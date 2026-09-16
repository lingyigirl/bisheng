"""F057: assigning a library to a department is a privilege-managed operation.

Setting `knowledge.department_id` GRANTS access — it hands the library to every
member of that department and its descendants (read-time rule in
`PermissionService._implicit_department_library_level`). So it is an
authorization change, not metadata editing, and it needs a stronger permission
than `edit_kb`:

  * the actor must hold a `manage_kb_*` permission on the library (or be an
    admin), and
  * a non-admin may only target departments within their own subtree — matching
    the scope rule already used for knowledge_space department grants.

Only organization libraries (type=0/1) participate: `knowledge_space` has its
own binding table, and the personal (type=2) library is not an assignable
resource.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from bisheng.database.models.department import DepartmentDao, UserDepartmentDao
from bisheng.knowledge.domain.models.knowledge import Knowledge, KnowledgeDao, KnowledgeTypeEnum
from bisheng.knowledge.domain.services.knowledge_department_service import KnowledgeDepartmentService

_ACTOR = SimpleNamespace(user_id=7, is_admin=lambda: False)
_ADMIN = SimpleNamespace(user_id=7, is_admin=lambda: True)


def _library(type_: int = KnowledgeTypeEnum.NORMAL.value, department_id=None) -> Knowledge:
    """A real ORM instance — `KnowledgeDao.update_one` requires a mapped object."""
    return Knowledge(
        id=55,
        user_id=9,
        tenant_id=1,
        name="lib",
        type=type_,
        department_id=department_id,
    )


def _dept(id_: int, path: str):
    return SimpleNamespace(id=id_, department_id=id_, path=path, name=f"dept{id_}")


def _membership(department_id: int):
    return SimpleNamespace(department_id=department_id, user_id=7)


def _patch(*, library=None, actor_depts=None, target=_dept(30, "/1/30/"), has_manage=True):
    """Patch every DB read the service performs, returning `(patchers, library)`.

    `_actor_department_paths` reads UserDepartmentDao FIRST and then resolves the
    departments — patching only `DepartmentDao.aget_by_ids` leaves the membership
    lookup returning a bare MagicMock, which iterates as empty and makes the
    scope check fail for the wrong reason.

    The library instance is returned so callers can assert on the value the
    service ACTUALLY wrote. Asserting only the `(allowed, error)` tuple lets a
    wrong write pass: setting `department_id = 999` instead of 30, or not
    assigning at all, would leave every such test green.
    """
    library = _library() if library is None else library
    actor_depts = actor_depts if actor_depts is not None else []
    patchers = [
        patch.object(KnowledgeDao, "aquery_by_id", new_callable=AsyncMock, return_value=library),
        patch.object(KnowledgeDao, "update_one", new_callable=AsyncMock, return_value=library),
        patch.object(DepartmentDao, "aget_by_id", new_callable=AsyncMock, return_value=target),
        patch.object(
            UserDepartmentDao,
            "aget_user_departments",
            new_callable=AsyncMock,
            return_value=[_membership(d.id) for d in actor_depts],
        ),
        patch.object(DepartmentDao, "aget_by_ids", new_callable=AsyncMock, return_value=actor_depts),
        patch.object(
            KnowledgeDepartmentService,
            "_has_manage_permission",
            new_callable=AsyncMock,
            return_value=has_manage,
        ),
    ]
    return patchers, library


async def _assign(actor=_ACTOR, department_id=30):
    return await KnowledgeDepartmentService.assign_department(
        login_user=actor,
        knowledge_id=55,
        department_id=department_id,
    )


@pytest.mark.asyncio
async def test_manage_holder_may_assign_to_own_department():
    """The actor's own department is always in scope."""
    patchers, library = _patch(actor_depts=[_dept(30, "/1/30/")], target=_dept(30, "/1/30/"))
    for p in patchers:
        p.start()
    try:
        allowed, error = await _assign()
        assert error is None
        assert allowed is True
        # The write itself, not just the verdict — a wrong target would otherwise
        # pass every assertion above.
        assert library.department_id == 30, f'wrote {library.department_id}, expected 30'
    finally:
        for p in patchers:
            p.stop()


@pytest.mark.asyncio
async def test_manage_holder_may_assign_to_a_descendant():
    """Handing a library DOWN to a child department is the core use case.

    This is the direction the filter tree is built around (a member sees their
    own department plus ancestors), so it must be permitted.
    """
    patchers, library = _patch(actor_depts=[_dept(30, "/1/30/")], target=_dept(31, "/1/30/31/"))
    for p in patchers:
        p.start()
    try:
        allowed, error = await _assign(department_id=31)
        assert error is None, f'assigning to a child department was refused: {error}'
        assert allowed is True
        assert library.department_id == 31, f'wrote {library.department_id}, expected 31'
    finally:
        for p in patchers:
            p.stop()


@pytest.mark.asyncio
async def test_manage_holder_cannot_assign_to_an_ancestor():
    """UPWARD is out of scope, and this is the direction that matters most.

    The prefix test reads naturally either way round, so an inverted comparison
    would silently allow this — and an ancestor is typically closer to the
    tenant root, so the mistake hands the library to a strictly larger audience
    (at the root: every tenant member). The previous implementation had exactly
    that inversion, and this case was not covered.
    """
    patchers, library = _patch(actor_depts=[_dept(31, "/1/30/31/")], target=_dept(30, "/1/30/"))
    for p in patchers:
        p.start()
    try:
        allowed, error = await _assign()
        assert allowed is False, 'a library was published to an ancestor department'
        assert error is not None
        assert library.department_id is None, 'a refused assignment mutated the library'
    finally:
        for p in patchers:
            p.stop()


@pytest.mark.asyncio
async def test_manage_holder_cannot_assign_to_a_sibling_subtree():
    """Scope guard: managing a library must not let you hand it to a foreign dept."""
    patchers, library = _patch(actor_depts=[_dept(50, "/1/50/")], target=_dept(30, "/1/30/"))
    for p in patchers:
        p.start()
    try:
        allowed, error = await _assign()
        assert allowed is False
        assert error is not None
        # A refusal must not write. Without this, a guard that reports failure
        # AFTER mutating the row would still look correct.
        assert library.department_id is None, 'a refused assignment mutated the library'
    finally:
        for p in patchers:
            p.stop()


@pytest.mark.asyncio
async def test_non_manager_cannot_assign_even_inside_own_department():
    """An editor (edit_kb but no manage_kb_*) must not change authorization."""
    patchers, library = _patch(actor_depts=[_dept(31, "/1/30/31/")], target=_dept(30, "/1/30/"), has_manage=False)
    for p in patchers:
        p.start()
    try:
        allowed, error = await _assign()
        assert allowed is False
        assert error is not None
        # A refusal must not write: a guard that reports failure AFTER mutating
        # the row would otherwise look correct.
        assert library.department_id is None, 'a refused assignment mutated the library'
    finally:
        for p in patchers:
            p.stop()


@pytest.mark.asyncio
async def test_admin_may_assign_any_department():
    """Admins bypass BOTH the manage check and the subtree guard.

    Their department list is deliberately empty here: an admin with no
    department must still be able to assign.
    """
    patchers, library = _patch(actor_depts=[], target=_dept(30, "/1/30/"))
    for p in patchers:
        p.start()
    try:
        allowed, error = await _assign(actor=_ADMIN)
        assert allowed is True
        assert error is None
        assert library.department_id == 30, f'admin write did not land: {library.department_id}'
    finally:
        for p in patchers:
            p.stop()


@pytest.mark.asyncio
async def test_clearing_the_department_is_allowed():
    """`department_id=None` unclassifies — same privilege, no department lookup."""
    patchers, library = _patch(actor_depts=[_dept(31, "/1/30/31/")])
    for p in patchers:
        p.start()
    try:
        allowed, error = await _assign(department_id=None)
        assert allowed is True
        assert error is None
        assert library.department_id is None, f'expected unclassified, got {library.department_id}'
    finally:
        for p in patchers:
            p.stop()


@pytest.mark.asyncio
async def test_knowledge_space_is_not_assignable():
    """knowledge_space has its own binding table — reject it here."""
    space = _library(type_=KnowledgeTypeEnum.SPACE.value)
    patchers, library = _patch(library=space, actor_depts=[_dept(31, "/1/30/31/")])
    for p in patchers:
        p.start()
    try:
        allowed, error = await _assign()
        assert allowed is False
        assert error is not None
        # A refusal must not write: a guard that reports failure AFTER mutating
        # the row would otherwise look correct.
        assert library.department_id is None, 'a refused assignment mutated the library'
    finally:
        for p in patchers:
            p.stop()


@pytest.mark.asyncio
async def test_personal_library_is_not_assignable():
    personal = _library(type_=KnowledgeTypeEnum.PRIVATE.value)
    patchers, library = _patch(library=personal, actor_depts=[_dept(31, "/1/30/31/")])
    for p in patchers:
        p.start()
    try:
        allowed, error = await _assign()
        assert allowed is False
        assert error is not None
        # A refusal must not write: a guard that reports failure AFTER mutating
        # the row would otherwise look correct.
        assert library.department_id is None, 'a refused assignment mutated the library'
    finally:
        for p in patchers:
            p.stop()


@pytest.mark.asyncio
async def test_missing_library_is_reported():
    patchers, library = _patch(library=None)
    for p in patchers:
        p.start()
    try:
        allowed, error = await _assign()
        assert allowed is False
        assert error is not None
        # A refusal must not write: a guard that reports failure AFTER mutating
        # the row would otherwise look correct.
        assert library.department_id is None, 'a refused assignment mutated the library'
    finally:
        for p in patchers:
            p.stop()


@pytest.mark.asyncio
async def test_unknown_target_department_is_reported():
    patchers, library = _patch(actor_depts=[_dept(31, "/1/30/31/")], target=None)
    for p in patchers:
        p.start()
    try:
        allowed, error = await _assign()
        assert allowed is False
        assert error is not None
        # A refusal must not write: a guard that reports failure AFTER mutating
        # the row would otherwise look correct.
        assert library.department_id is None, 'a refused assignment mutated the library'
    finally:
        for p in patchers:
            p.stop()
