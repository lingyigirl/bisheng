"""F061 T-32/T-38: a datasource cannot be granted to a DEPARTMENT.

Spec AC-45 enumerates the grantees ("指定用户与用户组") and AC-47 forbids
introducing department-ownership semantics at all. The UI stopped offering the
department tab for `db_datasource` in T-38, but that was only ever half the
requirement: `validate_resource_grants` accepted a department subject for EVERY
resource type, so a direct API call could still write the tuple. Hiding an
option is not a rule.

Measured before the fix (the assertion below is what fails on the old code):
`PUT /api/v1/permissions/resources/db_datasource/7/authorize` with a
`subject_type: "department"` grant was accepted.

The refusal lives here rather than at the endpoint so every caller is covered —
the endpoint is not the only writer, and a future one should not have to
remember.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from bisheng.common.errcode.permission import PermissionDeniedError
from bisheng.permission.domain.services.grant_subject_query_service import (
    GrantSubjectQueryService,
)

#: Subject kinds a datasource may be granted to. `department` is absent on
#: purpose: AC-47.
_ALLOWED = {'user', 'user_group'}


def _service(monkeypatch):
    """A service whose tenancy checks pass, so only the subject rule is tested."""
    service = GrantSubjectQueryService.__new__(GrantSubjectQueryService)
    repository = SimpleNamespace(
        is_active_tenant=AsyncMock(return_value=True),
        users_exist_in_tenant=AsyncMock(return_value=True),
        departments_exist_in_tenant=AsyncMock(return_value=True),
        user_groups_exist_in_tenant=AsyncMock(return_value=True),
    )
    service.repository = repository
    monkeypatch.setattr(
        'bisheng.permission.domain.services.permission_service.PermissionService._resolve_resource_tenant',
        AsyncMock(return_value=1),
    )
    return service


async def _validate(service, grants):
    await service.validate_resource_grants(
        resource_type='db_datasource', resource_id='7', grants=grants
    )


async def test_a_department_grant_is_refused(monkeypatch):
    """The gap itself. A department subject must be rejected for a datasource."""
    service = _service(monkeypatch)
    grants = [SimpleNamespace(subject_type='department', subject_id=5)]

    with pytest.raises(PermissionDeniedError):
        await _validate(service, grants)


async def test_user_and_user_group_grants_are_still_accepted(monkeypatch):
    """AC-45's actual grantee list must keep working — a fix that refused these
    would satisfy AC-47 by breaking the feature."""
    service = _service(monkeypatch)
    grants = [
        SimpleNamespace(subject_type='user', subject_id=5),
        SimpleNamespace(subject_type='user_group', subject_id=6),
    ]

    await _validate(service, grants)


async def test_other_resource_types_still_accept_departments(monkeypatch):
    """The rule is scoped to datasources. Departments are a first-class subject
    for knowledge libraries and the rest — narrowing them too would be a much
    larger change than AC-47 asks for, and would break grants that exist today.
    """
    service = _service(monkeypatch)
    grants = [SimpleNamespace(subject_type='department', subject_id=5)]

    for resource_type in ('knowledge_library', 'knowledge_space', 'workflow', 'assistant'):
        await service.validate_resource_grants(
            resource_type=resource_type, resource_id='7', grants=grants
        )
