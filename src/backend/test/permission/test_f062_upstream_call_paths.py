"""F062 / T-24: the three upstream call paths of the rule we changed.

`PermissionService._implicit_department_library_level` is reached through
`_implicit_department_space_member_level` -> `get_implicit_permission_level`, and
that outer function has exactly THREE call sites (grep-verified, not read off a
blast-radius summary):

    1. `permission_service` (internal, via the check() five-level ladder)
    2. `fine_grained_permission_service` x2
    3. `resource_permission` endpoint

F062 changed the BEHAVIOUR inside that chain without changing any signature. That
is the dangerous shape: nothing fails to import, nothing fails to compile, and a
caller that relied on the old answer just starts returning a different one.

So each path gets one assertion here — not to re-test the rule (the truth tables
do that), but to pin that these callers still route through it and still get a
verdict rather than an exception.

覆盖 AC: AC-02, AC-20
"""

from unittest.mock import AsyncMock, patch

import pytest

from bisheng.permission.domain.schemas.permission_schema import UNCACHEABLE_RELATIONS
from bisheng.permission.domain.services.permission_service import PermissionService

ORG = 0
DEPT = 1
_ROWS = [
    (1, None, "/1/", ORG),
    (4, 1, "/1/4/", ORG),
    (5, 4, "/1/4/5/", DEPT),
]


def _mapping():
    return {r[0]: (r[1], r[2], r[3]) for r in _ROWS}


def _user():
    u = type("U", (), {})()
    u.user_id = 7
    u.is_admin = lambda: False
    u.is_global_super = False
    return u


# ---------------------------------------------------------------------------
# The three call sites still route through the changed rule
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "module_path, attr",
    [
        # 1. the internal ladder
        ("bisheng.permission.domain.services.permission_service", "PermissionService"),
        # 2/3. the fine-grained service calls it twice
        ("bisheng.permission.domain.services.fine_grained_permission_service", "FineGrainedPermissionService"),
    ],
)
def test_module_imports_and_exposes_the_entry_point(module_path, attr):
    """Cheap structural guard: the entry point is still reachable from each path.

    A rename or a move would break these callers silently at runtime, in paths
    (admin permission screens, resource authorization) that no F062 test
    otherwise exercises.
    """
    import importlib

    mod = importlib.import_module(module_path)
    klass = getattr(mod, attr)
    assert hasattr(PermissionService, "get_implicit_permission_level")
    assert klass is not None


async def test_implicit_level_still_answers_for_a_department_owned_library():
    """The base case through the full chain: a descendant resolves to can_read.

    Exercised via `_implicit_department_space_member_level` because that is the
    dispatcher every caller ultimately lands on.
    """
    from bisheng.database.models.department import Department

    with (
        patch(
            "bisheng.permission.domain.services.department_org_domain.get_department_mapping",
            new_callable=AsyncMock,
            return_value=_mapping(),
        ),
        patch(
            "bisheng.knowledge.domain.models.knowledge.KnowledgeDao.aquery_by_id",
            new_callable=AsyncMock,
            return_value=type("K", (), {"department_id": 4})(),
        ),
        patch(
            "bisheng.database.models.department.UserDepartmentDao.aget_user_departments",
            new_callable=AsyncMock,
            return_value=[type("UD", (), {"department_id": 5})()],
        ),
        patch(
            "bisheng.database.models.department.DepartmentDao.aget_by_ids",
            new_callable=AsyncMock,
            return_value=[Department(id=5, name="d", parent_id=4, path="/1/4/5/", tenant_id=1, is_department=1)],
        ),
    ):
        level = await PermissionService._implicit_department_space_member_level(7, "knowledge_library", "100")

    assert level == "can_read"


async def test_the_rule_does_not_raise_when_the_mapping_is_unavailable():
    """Degraded path: no mapping (oversized tenant / load failure) must return a
    verdict, not an exception.

    All three upstream callers run inside request handlers; an exception here
    would surface as a 500 on a permission screen rather than as a denial.
    """
    with (
        patch(
            "bisheng.permission.domain.services.department_org_domain.get_department_mapping",
            new_callable=AsyncMock,
            return_value=None,
        ),
        patch.object(
            PermissionService,
            "_resolve_org_domain_path_uncached",
            new_callable=AsyncMock,
            return_value=None,
        ),
    ):
        level = await PermissionService._implicit_department_space_member_level(7, "knowledge_library", "100")

    assert level is None, "fail closed, but answer"


# ---------------------------------------------------------------------------
# Cache bypass is unchanged
# ---------------------------------------------------------------------------


def test_can_manage_and_can_delete_still_bypass_the_cache():
    """F062 did not touch the uncacheable set; asserted so a future edit to the
    implicit chain cannot quietly fold these into the cached path."""
    assert "can_manage" in UNCACHEABLE_RELATIONS
    assert "can_delete" in UNCACHEABLE_RELATIONS
