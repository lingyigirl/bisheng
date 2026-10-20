"""F078 T-09b: permission-id mapping and grantable resource type."""

from bisheng.open_api.domain.services.resource_grant_service import GRANTABLE_RESOURCE_TYPES
from bisheng.permission.api.endpoints import resource_permission
from bisheng.permission.domain.application_permission_template import (
    default_permission_ids_for_relation as default_application_permissions,
)
from bisheng.permission.domain.schemas.permission_schema import VALID_RESOURCE_TYPES
from bisheng.permission.domain.services import resource_authorization_service
from bisheng.permission.domain.services.fine_grained_permission_service import (
    FineGrainedPermissionService,
)


def test_managed_agent_is_a_valid_resource_type():
    assert "managed_agent" in VALID_RESOURCE_TYPES


def test_fine_grained_ids_match_application_template():
    for relation in ("viewer", "editor", "manager", "owner"):
        assert FineGrainedPermissionService.default_permission_ids_for_relation(
            "managed_agent", relation
        ) == default_application_permissions(relation)


def test_resource_authorization_ids_match_application_template():
    for relation in ("viewer", "editor", "manager", "owner"):
        assert resource_authorization_service._default_permission_ids("managed_agent", relation) == set(
            default_application_permissions(relation)
        )


def test_managed_agent_is_grantable():
    assert "managed_agent" in GRANTABLE_RESOURCE_TYPES


def test_manage_tier_maps_agree():
    """The map is duplicated in two modules; a drift would make the UI offer
    management permissions the backend does not understand."""
    assert (
        resource_authorization_service._MANAGE_PERMISSION_BY_RESOURCE_TIER["managed_agent"]
        == resource_permission._MANAGE_PERMISSION_BY_RESOURCE_TIER["managed_agent"]
    )
    assert (
        resource_authorization_service._MANAGE_PERMISSION_BY_RESOURCE_TIER["managed_agent"]["owner"]
        == "manage_app_owner"
    )
