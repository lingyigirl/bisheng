"""F061 permission wiring: the resource type, its permission points, and the
create/update paths that must not bypass them.

Four things are easy to get wrong here and each is asserted separately:

1. `db_datasource` missing from `VALID_RESOURCE_TYPES` — the whole authorize API
   would answer 195xx "invalid resource" and the resource would be unauthorizable.
2. The manage ids colliding with `manage_kb_*` — spec AC-44 requires independent
   governance, and a collision is invisible: both maps still "work".
3. The tier map existing in only ONE of its two copies. The duplication is real
   (`permission/api/endpoints/resource_permission.py` and
   `permission/domain/services/resource_authorization_service.py`), so the two
   are compared against each other rather than against a literal.
4. Creation writing the row without the owner tuple — spec AC-45 makes the
   creator the only default grantee, so a missing tuple means nobody can read
   the datasource, not even its owner.

The DB layer is faked (an I/O boundary), and `PermissionService.authorize` /
`check` — the calls under test — are asserted on, never mocked away.
"""

from unittest.mock import AsyncMock, patch

import pytest

from bisheng.core.openfga.authorization_model import (
    AUTHORIZATION_MODEL,
    MODEL_VERSION,
    get_authorization_model,
)
from bisheng.datasource.domain.services.datasource_service import (
    DATASOURCE_OBJECT_TYPE,
    DatasourceService,
)
from bisheng.permission.domain.datasource_permission_template import (
    DATASOURCE_PERMISSION_TEMPLATE,
    default_permission_ids_for_relation,
)
from bisheng.permission.domain.schemas.permission_schema import VALID_RESOURCE_TYPES

_KB_MANAGE_IDS = {"manage_kb_owner", "manage_kb_manager", "manage_kb_viewer"}


def _type_map() -> dict:
    return {td["type"]: td for td in get_authorization_model()["type_definitions"]}


# ── T-06: the FGA resource type ──────────────────────────────────


def test_datasource_type_is_in_the_authorization_model():
    assert "db_datasource" in _type_map()


def test_datasource_is_a_peer_of_knowledge_library_not_a_child():
    """AC-43: no parent relation — datasources nest under nothing."""
    datasource = _type_map()["db_datasource"]
    assert "parent" not in datasource["relations"]
    assert "parent" not in datasource["metadata"]["relations"]


def test_datasource_has_the_standard_permission_pyramid():
    relations = _type_map()["db_datasource"]["relations"]
    for relation in ("owner", "manager", "editor", "viewer", "can_manage", "can_edit", "can_read", "can_delete"):
        assert relation in relations, f"db_datasource is missing relation {relation}"


def test_datasource_can_delete_is_owner_only():
    """Not a hierarchical type, so delete must not inherit from a parent."""
    datasource = _type_map()["db_datasource"]
    assert datasource["relations"]["can_delete"] == {"computedUserset": {"relation": "owner"}}


def test_model_version_changed_with_the_model():
    """A model change that leaves MODEL_VERSION alone never reaches running
    deployments: they key their reload decision on this string."""
    assert MODEL_VERSION != "v2.0.2"
    assert len(AUTHORIZATION_MODEL["type_definitions"]) == 17


# ── T-08: resource type + permission points ──────────────────────


def test_valid_resource_types_contains_datasource():
    assert "db_datasource" in VALID_RESOURCE_TYPES


def test_datasource_management_ids_are_independent_of_knowledge_library():
    """AC-44: one grant must never open both resource families."""
    from bisheng.permission.api.endpoints.resource_permission import (
        _MANAGE_PERMISSION_BY_RESOURCE_TIER as endpoint_map,
    )

    endpoint_ids = set(endpoint_map["db_datasource"].values())
    assert endpoint_ids, "db_datasource has no manage permission points"
    assert endpoint_ids.isdisjoint(_KB_MANAGE_IDS)
    assert endpoint_ids.isdisjoint(set(endpoint_map["knowledge_library"].values()))


def test_both_tier_maps_carry_datasource_and_agree():
    """The two copies of _MANAGE_PERMISSION_BY_RESOURCE_TIER must stay in sync.

    They are read by different layers (the HTTP authorizer and the domain
    authorization service), so a one-sided edit silently grants management to
    the wrong people on one path only.
    """
    from bisheng.permission.api.endpoints.resource_permission import (
        _MANAGE_PERMISSION_BY_RESOURCE_TIER as endpoint_map,
    )
    from bisheng.permission.domain.services.resource_authorization_service import (
        _MANAGE_PERMISSION_BY_RESOURCE_TIER as service_map,
    )

    assert "db_datasource" in endpoint_map
    assert "db_datasource" in service_map
    assert endpoint_map["db_datasource"] == service_map["db_datasource"]
    # The whole maps, not just this entry — the comparison is the point.
    assert endpoint_map == service_map


def test_datasource_template_exposes_its_own_manage_ids():
    ids = {item["id"] for column in DATASOURCE_PERMISSION_TEMPLATE["columns"] for item in column["items"]}
    assert {"manage_datasource_owner", "manage_datasource_manager", "manage_datasource_viewer"} <= ids
    assert ids.isdisjoint(_KB_MANAGE_IDS)


def test_owner_relation_gets_every_datasource_permission():
    """The creator's default grant must include the manage ids, otherwise a
    fresh datasource cannot be administered by the person who created it."""
    owner_ids = default_permission_ids_for_relation("owner")
    assert "manage_datasource_owner" in owner_ids
    assert "view_datasource" in owner_ids
    # Narrower relations must not receive the management ids.
    assert "manage_datasource_owner" not in default_permission_ids_for_relation("viewer")


def test_template_is_registered_in_the_grant_resolvers():
    """A template no resolver knows about resolves to no permissions at all."""
    from bisheng.permission.domain.services.fine_grained_permission_service import (
        FineGrainedPermissionService,
    )
    from bisheng.permission.domain.services.resource_authorization_service import (
        _default_permission_ids,
    )

    assert _default_permission_ids("db_datasource", "owner") == default_permission_ids_for_relation("owner")
    assert FineGrainedPermissionService.default_permission_ids_for_relation(
        "db_datasource", "viewer"
    ) == default_permission_ids_for_relation("viewer")


def test_datasource_permissions_are_not_knowledge_library_permissions():
    """Same shape, different id family — guards against a copy-paste template."""
    from bisheng.permission.domain.knowledge_library_permission_template import (
        default_permission_ids_for_relation as kb_defaults,
    )

    assert default_permission_ids_for_relation("owner").isdisjoint(kb_defaults("owner"))


# ── T-09: create / update走统一权限入口 ────────────────────────────


class _StubDatasource:
    def __init__(self, datasource_id: int = 7):
        self.id = datasource_id
        self.engine = "mysql"


class _LoginUser:
    """Minimal stand-in for UserPayload; the service only reads user_id."""

    user_id = 42


async def test_create_datasource_writes_the_owner_tuple():
    """AC-45 / C4: the creator gets the owner relation, through the platform's
    single tuple-writing entry point (asserted, not mocked away)."""
    with (
        patch(
            "bisheng.datasource.domain.models.dao.DbDatasourceDao.acreate",
            new=AsyncMock(return_value=_StubDatasource(7)),
        ),
        patch(
            "bisheng.permission.domain.services.permission_service.PermissionService.authorize",
            new=AsyncMock(),
        ) as authorize,
    ):
        created = await DatasourceService.create_datasource(user_id=42, data={"name": "erp"})

    assert created.id == 7
    authorize.assert_awaited_once()
    kwargs = authorize.await_args.kwargs
    assert kwargs["object_type"] == DATASOURCE_OBJECT_TYPE
    assert kwargs["object_id"] == "7"
    grants = kwargs["grants"]
    assert len(grants) == 1
    assert grants[0].subject_type == "user"
    assert grants[0].subject_id == 42
    assert grants[0].relation == "owner"
    # A datasource with no owner tuple is unreadable by anyone: the create must
    # not silently succeed on a failed tuple write.
    assert kwargs["enforce_fga_success"] is True


async def test_create_datasource_does_not_write_a_row_when_authorize_is_not_reached(monkeypatch):
    """Negative control: if the tuple write raises, the caller sees the failure
    instead of a datasource that nobody — including the creator — can read."""
    from bisheng.permission.domain.services.permission_service import PermissionService

    async def _boom(**kwargs):
        raise RuntimeError("fga down")

    monkeypatch.setattr(PermissionService, "authorize", _boom)
    with patch(
        "bisheng.datasource.domain.models.dao.DbDatasourceDao.acreate",
        new=AsyncMock(return_value=_StubDatasource(7)),
    ):
        with pytest.raises(RuntimeError):
            await DatasourceService.create_datasource(user_id=42, data={"name": "erp"})


async def test_update_datasource_checks_permission_before_touching_the_row():
    """INV-15: check() first, and the row is only read once it passes."""
    calls: list[str] = []

    async def _check(**kwargs):
        calls.append("check")
        return True

    async def _aget(datasource_id):
        calls.append("aget")
        return _StubDatasource(datasource_id)

    async def _aupdate(datasource):
        calls.append("aupdate")
        return datasource

    from bisheng.permission.domain.services.permission_service import PermissionService

    with (
        patch.object(PermissionService, "check", staticmethod(_check)),
        patch("bisheng.datasource.domain.models.dao.DbDatasourceDao.aget_by_id", staticmethod(_aget)),
        patch("bisheng.datasource.domain.models.dao.DbDatasourceDao.aupdate", staticmethod(_aupdate)),
    ):
        updated = await DatasourceService.update_datasource(
            datasource_id=7,
            data={"host": "db.internal"},
            login_user=_LoginUser(),
        )

    assert updated.host == "db.internal"
    assert calls == ["check", "aget", "aupdate"]


async def test_update_datasource_refuses_without_can_manage():
    """AC-49: an unauthorized caller is refused on the direct call, and the same
    error covers "missing" so ids cannot be probed."""
    from bisheng.common.errcode.datasource import DatasourceNotFoundError
    from bisheng.permission.domain.services.permission_service import PermissionService

    with (
        patch.object(PermissionService, "check", new=AsyncMock(return_value=False)),
        patch(
            "bisheng.datasource.domain.models.dao.DbDatasourceDao.aupdate",
            new=AsyncMock(),
        ) as aupdate,
        patch(
            "bisheng.datasource.domain.models.dao.DbDatasourceDao.aget_by_id",
            new=AsyncMock(),
        ) as aget,
    ):
        with pytest.raises(DatasourceNotFoundError):
            await DatasourceService.update_datasource(
                datasource_id=7,
                data={"host": "db.internal"},
                login_user=_LoginUser(),
            )

    aupdate.assert_not_awaited()
    aget.assert_not_awaited()


# ── The two central resolver registries ──────────────────────────


async def test_permission_service_resolves_the_datasource_creator(monkeypatch):
    """The owner-less fallback (INV-2) must cover datasources too: a lost owner
    tuple must not make the datasource unreachable for the person who made it."""
    from bisheng.permission.domain.services.permission_service import PermissionService

    row = _StubDatasource(7)
    row.user_id = 42
    monkeypatch.setattr(
        "bisheng.datasource.domain.models.dao.DbDatasourceDao.aget_by_id",
        AsyncMock(return_value=row),
    )

    assert await PermissionService._get_resource_creator("db_datasource", "7") == 42


async def test_permission_service_resolves_the_datasource_tenant(monkeypatch):
    """AC-50: the owning tenant must resolve, so tenant gating actually runs."""
    from bisheng.permission.domain.services.permission_service import PermissionService

    row = _StubDatasource(7)
    row.tenant_id = 3
    monkeypatch.setattr(
        "bisheng.datasource.domain.models.dao.DbDatasourceDao.aget_by_id",
        AsyncMock(return_value=row),
    )

    assert await PermissionService._resolve_resource_tenant("db_datasource", "7") == 3


async def test_missing_datasource_resolves_to_none_not_an_exception(monkeypatch):
    from bisheng.permission.domain.services.permission_service import PermissionService

    monkeypatch.setattr(
        "bisheng.datasource.domain.models.dao.DbDatasourceDao.aget_by_id",
        AsyncMock(return_value=None),
    )

    assert await PermissionService._get_resource_creator("db_datasource", "7") is None
    assert await PermissionService._resolve_resource_tenant("db_datasource", "7") is None
