"""Datasource lifecycle rules shared by every entry point (F061).

**Creation is the one place the owner tuple is written.** A `db_datasource` row
with no owner tuple is invisible even to its creator: spec AC-45 makes the
creator the only default grantee, and that grant *is* the tuple. So creation
goes through the platform's F008 integration point
(`OwnerService.write_owner_tuple`, the same call assistant / tool /
knowledge_library creation uses) rather than writing a row and hoping. The write
is ``enforce_fga_success=True``: the legacy best-effort default would leave a
silently unreadable datasource behind on failure, which for this resource type
is strictly worse than failing the create.

**Mutation checks before it writes** (v2.5.0 INV-15, constitution C4). Editing a
datasource's connection details decides which customer data leaves the database,
so it requires ``can_manage`` — the manager tier covered by
``manage_datasource_manager`` (spec AC-44) — not the read/edit tier.

This module is the seam the API layer calls; it deliberately contains no HTTP
concerns and no credential handling (spec AC-04 encryption happens before the
payload reaches here).
"""

from __future__ import annotations

from typing import Any

from loguru import logger

from bisheng.common.dependencies.user_deps import UserPayload
from bisheng.common.errcode.datasource import DatasourceNotFoundError
from bisheng.core.context.tenant import strict_tenant_filter
from bisheng.datasource.domain.models.dao import DbDatasourceDao
from bisheng.datasource.domain.models.datasource import DbDatasource
from bisheng.permission.domain.services.owner_service import OwnerService
from bisheng.permission.domain.services.permission_service import PermissionService

# OpenFGA object type for a registered database (spec AC-43). Same string as the
# authorization-model type, the VALID_RESOURCE_TYPES entry and the tier maps.
DATASOURCE_OBJECT_TYPE = "db_datasource"


class DatasourceService:
    """Stateless service; all methods are classmethods."""

    @classmethod
    async def create_datasource(
        cls,
        *,
        user_id: int,
        data: dict[str, Any],
    ) -> DbDatasource:
        """Persist a datasource and register its creator as owner.

        The owner tuple is written AFTER the row commit on purpose: the tuple
        needs the row id, and OpenFGA is not part of the DB transaction. A
        failure here raises (``enforce_fga_success=True``) instead of returning
        a datasource nobody can reach; the orphan row is visible to the
        operator, and deleting/re-creating is the documented recovery.
        """
        datasource = await DbDatasourceDao.acreate({**data, "user_id": user_id})
        await OwnerService.write_owner_tuple(
            user_id,
            DATASOURCE_OBJECT_TYPE,
            str(datasource.id),
            enforce_fga_success=True,
        )
        logger.info(
            "datasource created id={} engine={} by user_id={}",
            datasource.id,
            datasource.engine,
            user_id,
        )
        return datasource

    @classmethod
    async def update_datasource(
        cls,
        *,
        datasource_id: int,
        data: dict[str, Any],
        login_user: UserPayload,
    ) -> DbDatasource:
        """Apply field updates to an existing datasource after a permission check.

        The check runs first and on its own (INV-15): the row is only read once
        the caller is known to hold ``can_manage``, so an unauthorized caller
        cannot use the response to tell an existing id from a missing one —
        both surface as the same error (spec AC-49's direct-invocation clause).
        """
        allowed = await PermissionService.check(
            user_id=login_user.user_id,
            relation="can_manage",
            object_type=DATASOURCE_OBJECT_TYPE,
            object_id=str(datasource_id),
            login_user=login_user,
        )
        if not allowed:
            raise DatasourceNotFoundError()

        with strict_tenant_filter():
            datasource = await DbDatasourceDao.aget_by_id(datasource_id)
        if datasource is None:
            raise DatasourceNotFoundError()

        for field, value in data.items():
            setattr(datasource, field, value)
        return await DbDatasourceDao.aupdate(datasource)
