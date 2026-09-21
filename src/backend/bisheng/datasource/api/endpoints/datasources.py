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

import asyncio
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
from bisheng.datasource.domain.services import schema_collector, schema_service
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
    name: str | None = Query(None, description='按名称模糊筛选'),
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

    `name` filters by substring, not by identity -- the filter box is how an
    admin finds one datasource among many, and a blank box must list everything
    rather than match a datasource literally named "".
    """
    rows, total = await DbDatasourceDao.alist(
        name=name, enabled_only=enabled_only, page=page, page_size=page_size
    )
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


@router.get('/available-datasources')
async def list_available_datasources(
    login_user: UserPayload = Depends(UserPayload.get_login_user),
):
    """Datasources the CALLER may use, for a picker (spec AC-49 / AC-53).

    Distinct from `GET /datasource` above, which is the admin list: that one is
    gated by `get_tenant_admin_user` and deliberately unfiltered, because showing
    an admin only their own datasources would make the management page useless.
    This one answers the opposite question — "what may *I* pick?" — and so it is
    open to any logged-in user and narrowed to their grants.

    The population matters and is why this exists rather than reusing the admin
    list: the flow editor at `/flow/:id` has no permission gate, so plenty of
    people who can open it cannot call the admin endpoint at all.

    The path is hyphenated, the same as `test-connection` and `query-failures`,
    and `test_no_route_can_be_shadowed_by_the_id_route` enforces that every
    literal segment under `/datasource/` stays that way. The id route also
    constrains itself to digits now (see the note above the id routes), so the
    two defences overlap — deliberately: a hyphenated path is safe even if
    someone later relaxes the convertor, and vice versa.

    Returns id and name only. A picker needs nothing else, and the connection
    details — including the encrypted password — must not reach a chat-side
    caller (INV-19 clause 1); `DatasourceRead` has no field for them.
    """
    from bisheng.api.v1.schemas import DatasourceRead
    from bisheng.datasource.domain.services.conversation_service import (
        authorized_datasource_ids,
    )

    # One page sized to a practical picker. A tenant with more datasources than
    # this is not a picker problem — that many is a governance problem, and the
    # admin page pages properly.
    page_size = 200
    candidates, _total = await DbDatasourceDao.alist(
        enabled_only=True, page=1, page_size=page_size
    )

    # The authorization set narrows the READ, so a datasource the caller may not
    # use is never loaded. `ids=[]` is the "authorized for nothing" case and must
    # stay a real query rather than being skipped — passing no `ids` at all is
    # the unfiltered read, which is the bug this shape avoids.
    allowed = sorted(await authorized_datasource_ids(login_user, [row.id for row in candidates]))
    rows, _total = await DbDatasourceDao.alist(
        ids=allowed, enabled_only=True, page=1, page_size=page_size
    )

    return resp_200(
        {
            'data': [
                DatasourceRead(
                    id=row.id,
                    name=row.name,
                    description=row.description,
                    engine=row.engine,
                ).model_dump()
                for row in rows
            ],
            'total': len(rows),
        }
    )


# --- the id routes -----------------------------------------------------------
#
# `:int`, not a bare `{datasource_id}`, and it is load-bearing. Starlette's
# default `str` convertor matches ANY single segment, and the router resolves a
# request against the FIRST route whose path regex and method both match — so a
# bare id route swallows every later sibling declared below it. That is not
# hypothetical: `/datasource/query-failures` was declared after these three and
# answered 422 `int_parsing: input "query-failures"` for as long as it shipped.
# Constraining the id to digits makes the two impossible to confuse, rather than
# making correctness depend on registration order (which the module registers as
# datasources -> test-connection -> failures, but a reorder would silently undo).
#
# `/{datasource_id:int}/...` sub-routes keep the bare form on purpose: the
# convertor only affects how the *last* segment is matched when it is the id,
# and a trailing literal segment already disambiguates those.
#
# Only the id routes carry it. The literal ones rely on being hyphenated
# (`test_no_route_can_be_shadowed_by_the_id_route` asserts the property directly).


@router.get('/{datasource_id:int}')
async def get_datasource(
    datasource_id: int,
    login_user: UserPayload = Depends(UserPayload.get_tenant_admin_user),
):
    with strict_tenant_filter():
        datasource = await DbDatasourceDao.aget_by_id(datasource_id)
    if datasource is None:
        raise DatasourceNotFoundError()
    return resp_200(to_safe_dict(datasource))


@router.put('/{datasource_id:int}')
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


@router.delete('/{datasource_id:int}')
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


# --- table metadata (T-36 / T-37) --------------------------------------------
#
# The table-management half of the admin page. All four go through
# `schema_service`, which owns the AC-12 rule: a re-collect refreshes the
# structure and must NOT touch `business_desc`. An endpoint that wrote the row
# itself could break that in one line, which is why none of them does.


class TableSelectionPayload(BaseModel):
    table_names: list[str] = Field(default_factory=list, description='勾选暴露给模型的表名')


class TableDescriptionPayload(BaseModel):
    business_desc: str | None = Field(default=None, description='人工维护的业务描述')


async def _require_manage(datasource_id: int, login_user) -> None:
    """Refuse unless the caller may manage this datasource.

    Every table endpoint goes through here: the metadata describes a datasource,
    so it is governed by the same `can_manage` relation the datasource itself
    is. Without this, the table list would be a way around the datasource's own
    permission check.
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


@router.get('/{datasource_id}/available-tables')
async def list_available_tables(
    datasource_id: int,
    login_user: UserPayload = Depends(UserPayload.get_tenant_admin_user),
):
    """Reflect the tables the configured account can see, for the picker (AC-09).

    **A live read of the customer database**, not the stored snapshot: this is
    the list an admin chooses FROM, so it has to show what is actually there
    right now -- including a table added since the last collect, which a
    snapshot-based list could not offer.

    Two-step flow, and this is step one:

      1. `GET /available-tables` -> what exists; the admin ticks boxes
      2. `POST /collect` with the ticked names -> structure is persisted (AC-10)

    Answers with names only. Column structure is deliberately NOT read here:
    the admin has not chosen a table yet, and reflecting every column of every
    accessible table on a 100-table schema is work nobody asked for. The
    structure is collected for the tables that get picked.
    """
    await _require_manage(datasource_id, login_user)

    with strict_tenant_filter():
        datasource = await DbDatasourceDao.aget_by_id(datasource_id)
    if datasource is None:
        raise DatasourceNotFoundError()

    # `list_tables` reflects through the connection; a failure here is the
    # customer's database being unreachable, which surfaces as the connect
    # error rather than an empty list -- an empty list would read as "your
    # account can see nothing".
    names = await asyncio.to_thread(schema_collector.list_tables, datasource)

    # What has already been collected, so the picker can pre-tick it.
    existing = await schema_service.list_tables_for_admin(datasource_id)
    collected = {row['table_name']: row['enabled'] for row in existing}

    return resp_200(
        {
            'data': [
                {'table_name': name, 'collected': name in collected, 'enabled': collected.get(name, 0)}
                for name in names
            ],
            'total': len(names),
        }
    )


@router.get('/{datasource_id}/tables')
async def list_datasource_tables(
    datasource_id: int,
    login_user: UserPayload = Depends(UserPayload.get_tenant_admin_user),
):
    """The collected tables with their descriptions (spec AC-57).

    Reads the stored snapshot rather than reflecting the customer's database:
    the snapshot is what the model sees, so it is what the admin should be
    judging their description against.
    """
    await _require_manage(datasource_id, login_user)
    return resp_200({'data': await schema_service.list_tables_for_admin(datasource_id)})


@router.post('/{datasource_id}/tables/select')
async def select_datasource_tables(
    datasource_id: int,
    payload: TableSelectionPayload,
    login_user: UserPayload = Depends(UserPayload.get_tenant_admin_user),
):
    """Set which collected tables are exposed to the model (spec AC-09).

    Unticking sets `enabled=0` rather than deleting: the admin's description
    survives, which is what makes an untick reversible.
    """
    await _require_manage(datasource_id, login_user)
    changed = await schema_service.set_exposed(datasource_id, payload.table_names)
    return resp_200({'changed': changed})


@router.post('/{datasource_id}/collect')
async def collect_datasource_tables(
    datasource_id: int,
    payload: TableSelectionPayload,
    login_user: UserPayload = Depends(UserPayload.get_tenant_admin_user),
):
    """Re-collect the named tables' structure and persist it (spec AC-12).

    The description is preserved by construction: the only write inside
    `refresh_tables` is the snapshot update. `truncated` is reported rather than
    swallowed -- a silently capped collect reads as "your schema is complete".
    """
    await _require_manage(datasource_id, login_user)

    with strict_tenant_filter():
        datasource = await DbDatasourceDao.aget_by_id(datasource_id)
    if datasource is None:
        raise DatasourceNotFoundError()

    result = await schema_service.refresh_tables(datasource, payload.table_names)
    return resp_200(result)


@router.put('/table-metadata/{table_id}')
async def update_table_description(
    table_id: int,
    payload: TableDescriptionPayload,
    login_user: UserPayload = Depends(UserPayload.get_tenant_admin_user),
):
    """Set one table's business description (spec AC-11).

    The path is `/table-metadata/{id}`, not `/tables/{id}`, deliberately: a bare
    `/datasource/tables/{x}` would be matched by `/datasource/{datasource_id}`
    depending on registration order, so the endpoint would sometimes receive the
    literal string "tables" as an id. Prefixing with a segment that no id can be
    keeps the two apart.

    Checked against the datasource the table belongs to -- looked up first,
    since a table id alone does not say whose table it is.
    """
    from bisheng.datasource.domain.models.dao import DbDatasourceTableDao

    with strict_tenant_filter():
        rows = await DbDatasourceTableDao.alist_by_ids([table_id])
    if not rows:
        raise DatasourceNotFoundError()

    await _require_manage(rows[0].datasource_id, login_user)
    await schema_service.update_description(table_id, payload.business_desc)
    return resp_200({'id': table_id})
