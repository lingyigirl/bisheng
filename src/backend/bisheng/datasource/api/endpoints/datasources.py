"""Datasource CRUD (spec AC-01, AC-04, AC-05, AC-06, AC-07).

Every response goes through `to_safe_dict`. That is not a style preference: the
function's own docstring says which endpoints must use it, because building a
response by listing fields is how a column added later reaches the client
unredacted. `password` is the field that matters today; the next one is
whatever gets added next.

Two shapes are deliberately absent from the write schemas:

  * **No raw connection string.** AC-07 forbids a JDBC URL / DSN input, and the
    reason is checkable: a DSN bypasses the engine field, so the platform would
    have no idea which dialect it is talking to, and the guard would have to
    guess.
  * **No SQL-as-a-queryable-object.** Also AC-07: the model writes the statement
    per question; there is no saved-SQL concept to store.

`connections` carries host/port/database/username/password as separate fields,
which is what makes the engine's own port default and the credential encryption
possible.
"""

# ruff: noqa: RUF001 -- field descriptions are Chinese text the UI shows;
# fullwidth punctuation is correct in them.

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from bisheng.common.dependencies.user_deps import UserPayload
from bisheng.common.errcode.datasource import (
    DatasourceNameDuplicateError,
    DatasourceNotFoundError,
    DatasourceUnsupportedEngineError,
)
from bisheng.common.schemas.api import resp_200
from bisheng.core.context.tenant import strict_tenant_filter
from bisheng.datasource.domain.models.dao import DbDatasourceDao, DbDatasourceTableDao
from bisheng.datasource.domain.models.datasource import SUPPORTED_ENGINES
from bisheng.datasource.domain.services.credentials import encrypt_password, to_safe_dict
from bisheng.datasource.domain.services.datasource_service import DatasourceService
from bisheng.datasource.domain.services.engine_registry import canonical_engine

router = APIRouter(prefix='/datasource', tags=['datasource'])


class DatasourceConnection(BaseModel):
    host: str = Field(..., description='主机')
    port: int | None = Field(default=None, description='端口，留空用引擎默认值')
    database_name: str = Field(..., description='库名')
    username: str = Field(..., description='账号')
    password: str | None = Field(default=None, description='密码；编辑时留空表示不修改')


class DatasourceCreatePayload(BaseModel):
    name: str = Field(..., min_length=1, max_length=128, description='数据源名称')
    engine: str = Field(..., description='数据库引擎')
    connection: DatasourceConnection
    description: str | None = Field(default=None, description='用途说明')


class DatasourceUpdatePayload(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=128)
    connection: DatasourceConnection | None = None
    description: str | None = None
    enabled: int | None = Field(default=None, ge=0, le=1)


def _validate_engine(engine: str) -> str:
    """Normalise and reject unsupported engines.

    SQLite is rejected by the same path (AC-06): it is simply not in the
    supported set, and its file path would become an implicit permission
    boundary -- a datasource pointing at a local file is not something an
    admin's authorization model can reason about.
    """
    canonical = canonical_engine(engine)
    if canonical not in SUPPORTED_ENGINES:
        raise DatasourceUnsupportedEngineError()
    return canonical


def _connection_fields(connection: DatasourceConnection, *, engine: str) -> dict[str, Any]:
    """Turn the connection block into row columns, encrypting the password.

    `port` is optional at the API and required in the row: the engine knows its
    own default (5236 for DM8, 50000 for DB2), and making the admin look it up
    is how a datasource ends up configured against the wrong port.
    """
    from bisheng.datasource.domain.services.engine_registry import spec_for

    fields: dict[str, Any] = {
        'host': connection.host,
        'database_name': connection.database_name,
        'username': connection.username,
        'port': connection.port or spec_for(engine).default_port,
    }
    if connection.password:
        fields['password'] = encrypt_password(connection.password)
    return fields


async def _reject_duplicate_name(name: str, *, exclude_id: int | None = None) -> None:
    """Names are unique per tenant (the row index is not, so this is the check).

    Soft-delete does not apply here -- datasources are hard-deleted -- so a name
    freed by a delete is immediately reusable, which is what an admin expects.
    """
    existing = await DbDatasourceDao.aget_by_name(name)
    if existing is not None and existing.id != exclude_id:
        raise DatasourceNameDuplicateError()


@router.get('')
async def list_datasources(
    *,
    enabled_only: bool = Query(False, description='仅显示启用中的数据源'),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    login_user: UserPayload = Depends(UserPayload.get_tenant_admin_user),
):
    """Page datasources, newest first (spec AC-57).

    Admin-gated rather than filtered by per-user grants: this is the management
    page, and AC-45's "only the creator can see it by default" applies to the
    CHAT picker, where `conversation_service` does the per-user check. Showing
    an admin only the datasources they personally created would make the
    management page useless to the person responsible for them.
    """
    rows, total = await DbDatasourceDao.alist(enabled_only=enabled_only, page=page, page_size=page_size)
    return resp_200(
        {
            'data': [to_safe_dict(row) for row in rows],
            'total': total,
            'page': page,
            'page_size': page_size,
        }
    )


@router.post('')
async def create_datasource(
    payload: DatasourceCreatePayload,
    login_user: UserPayload = Depends(UserPayload.get_tenant_admin_user),
):
    """Create a datasource. Persists nothing until the form is submitted.

    AC-05: a writable account is NOT rejected here. The platform's boundary is
    the account's own permissions (INV-19 clause 4), and refusing a writable
    account would push customers to bypass the feature -- so the detection
    (surfaced by test-connection) exists to WARN.
    """
    engine = _validate_engine(payload.engine)
    await _reject_duplicate_name(payload.name)

    data: dict[str, Any] = {
        'tenant_id': login_user.tenant_id,
        'name': payload.name,
        'engine': engine,
        'description': payload.description,
        'enabled': 1,
        **_connection_fields(payload.connection, engine=engine),
    }
    datasource = await DatasourceService.create_datasource(user_id=login_user.user_id, data=data)
    return resp_200(to_safe_dict(datasource))


@router.get('/{datasource_id}')
async def get_datasource(
    datasource_id: int,
    login_user: UserPayload = Depends(UserPayload.get_tenant_admin_user),
):
    with strict_tenant_filter():
        datasource = await DbDatasourceDao.aget_by_id(datasource_id)
    if datasource is None:
        raise DatasourceNotFoundError()
    return resp_200(to_safe_dict(datasource))


@router.put('/{datasource_id}')
async def update_datasource(
    datasource_id: int,
    payload: DatasourceUpdatePayload,
    login_user: UserPayload = Depends(UserPayload.get_tenant_admin_user),
):
    """Update a datasource. An absent password leaves the stored one alone.

    The connection object is optional as a whole too: the admin editing only
    the name should not have to resend the host and account.
    """
    data: dict[str, Any] = {}
    if payload.name is not None:
        await _reject_duplicate_name(payload.name, exclude_id=datasource_id)
        data['name'] = payload.name
    if payload.description is not None:
        data['description'] = payload.description
    if payload.enabled is not None:
        data['enabled'] = payload.enabled
    if payload.connection is not None:
        # The engine is not updatable: changing it would silently reinterpret
        # every stored field (a port that meant 3306 now means 5236). An admin
        # who needs a different engine creates a second datasource.
        with strict_tenant_filter():
            existing = await DbDatasourceDao.aget_by_id(datasource_id)
        if existing is None:
            raise DatasourceNotFoundError()
        data.update(_connection_fields(payload.connection, engine=existing.engine))

    datasource = await DatasourceService.update_datasource(
        datasource_id=datasource_id, data=data, login_user=login_user
    )
    return resp_200(to_safe_dict(datasource))


@router.delete('/{datasource_id}')
async def delete_datasource(
    datasource_id: int,
    login_user: UserPayload = Depends(UserPayload.get_tenant_admin_user),
):
    """Delete a datasource (spec AC-62).

    A hard delete, and the history stays readable: the query log, failure trail
    and any citation anchor reference this id with no foreign key, so removing
    the row makes the datasource unavailable to future turns while every past
    answer keeps rendering. Cascading would delete exactly the audit trail the
    spec wants kept.

    The permission check is `can_manage` on the datasource, the same relation
    the update path uses.
    """
    from bisheng.permission.domain.services.permission_service import PermissionService

    if not await PermissionService.check(
        user_id=login_user.user_id,
        relation='can_manage',
        object_type='db_datasource',
        object_id=str(datasource_id),
        login_user=login_user,
    ):
        raise DatasourceNotFoundError()

    with strict_tenant_filter():
        existing = await DbDatasourceDao.aget_by_id(datasource_id)
    if existing is None:
        raise DatasourceNotFoundError()

    await DbDatasourceTableDao.adelete_by_datasource(datasource_id)
    await DbDatasourceDao.adelete(datasource_id)
    return resp_200({'deleted': datasource_id})
