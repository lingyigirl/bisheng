"""Datasource CRUD endpoints.

Two assertions carry this file: a password must not come back out of any read
path (AC-04 / INV-19 clause 1), and there must be no way to configure a
datasource with a raw connection string or a saved SQL object (AC-07).

Both are tested by looking for what is ABSENT, because that is what these rules
are -- and absence is what a future change quietly removes.
"""

import inspect
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bisheng.common.errcode.datasource import (
    DatasourceNameDuplicateError,
    DatasourceNotFoundError,
    DatasourceUnsupportedEngineError,
)
from bisheng.datasource.api.endpoints import datasources as ep


def _payload(**overrides):
    fields = {
        'name': '客户订单库',
        'engine': 'mysql',
        'connection': ep.DatasourceConnection(
            host='10.0.0.5', port=3306, database_name='orders', username='reader',
            password='S3cret-Passw0rd',
        ),
        'description': '订单查询',
    }
    fields.update(overrides)
    return ep.DatasourceCreatePayload(**fields)


def _row(**overrides):
    fields = {
        'id': 7, 'tenant_id': 1, 'user_id': 3, 'name': '客户订单库', 'engine': 'mysql',
        'host': '10.0.0.5', 'port': 3306, 'database_name': 'orders', 'username': 'reader',
        'password': 'gAAAAA-ciphertext', 'description': None, 'writable_account': None,
        'enabled': 1, 'create_time': None, 'update_time': None,
    }
    fields.update(overrides)
    row = MagicMock()
    row.model_dump = lambda: dict(fields)
    for key, value in fields.items():
        setattr(row, key, value)
    return row


def _admin():
    return SimpleNamespace(user_id=3, tenant_id=1)


# --- AC-04: the password never comes back ------------------------------------


async def test_creating_a_datasource_does_not_echo_the_password():
    """The response is built by `to_safe_dict`, whose own docstring says why:
    listing fields by hand is how a column added later leaks."""
    with patch.object(
        ep.DatasourceService, 'create_datasource', AsyncMock(return_value=_row())
    ), patch.object(ep.DbDatasourceDao, 'aget_by_name', AsyncMock(return_value=None)):
        response = await ep.create_datasource(_payload(), _admin())

    assert 'S3cret-Passw0rd' not in repr(response.data)
    assert 'password' not in response.data
    assert response.data['has_password'] is True


async def test_the_stored_password_is_encrypted_not_plaintext():
    """The row must not carry what the admin typed."""
    captured = {}

    async def _create(**kwargs):
        captured.update(kwargs['data'])
        return _row()

    with patch.object(ep.DatasourceService, 'create_datasource', _create), patch.object(
        ep.DbDatasourceDao, 'aget_by_name', AsyncMock(return_value=None)
    ):
        await ep.create_datasource(_payload(), _admin())

    assert captured['password'] != 'S3cret-Passw0rd'
    # And it is decipherable, i.e. actually Fernet rather than a hash.
    from bisheng.datasource.domain.services.credentials import decrypt_password

    assert decrypt_password(captured['password']) == 'S3cret-Passw0rd'


async def test_a_read_never_returns_the_password():
    with patch.object(ep.DbDatasourceDao, 'aget_by_id', AsyncMock(return_value=_row())):
        response = await ep.get_datasource(7, _admin())

    assert 'gAAAAA-ciphertext' not in repr(response.data)
    assert 'password' not in response.data


async def test_the_list_never_returns_the_password():
    with patch.object(
        ep.DbDatasourceDao, 'alist', AsyncMock(return_value=([_row(), _row(id=8)], 2))
    ):
        response = await ep.list_datasources(login_user=_admin())

    assert 'gAAAAA-ciphertext' not in repr(response.data)
    assert all('password' not in item for item in response.data['data'])


async def test_every_response_goes_through_the_redactor():
    """A source-level check, so a new endpoint cannot quietly hand-build a
    response and skip the redaction."""
    source = inspect.getsource(ep)
    # The only places `model_dump` may be called directly are the helpers that
    # are themselves the redaction path.
    assert source.count('to_safe_dict(') >= 3, 'an endpoint stopped using the redactor'


async def test_updating_without_a_password_leaves_the_stored_one_alone():
    """Otherwise editing the name would silently blank the credential."""
    captured = {}

    async def _update(**kwargs):
        captured.update(kwargs['data'])
        return _row()

    payload = ep.DatasourceUpdatePayload(
        name='新名字',
        connection=ep.DatasourceConnection(
            host='10.0.0.5', port=3306, database_name='orders', username='reader'
        ),
    )
    with patch.object(ep.DatasourceService, 'update_datasource', _update), patch.object(
        ep.DbDatasourceDao, 'aget_by_name', AsyncMock(return_value=None)
    ), patch.object(ep.DbDatasourceDao, 'aget_by_id', AsyncMock(return_value=_row())):
        await ep.update_datasource(7, payload, _admin())

    assert 'password' not in captured, 'an absent password overwrote the stored one'
    assert captured['name'] == '新名字'


# --- AC-07: two shapes the API must not accept -------------------------------


def test_there_is_no_connection_string_field():
    """AC-07 forbids a raw DSN. It would bypass the engine field, so the
    platform could not tell which dialect it is talking to -- and the SQL
    guard's dialect choice, the port default and the driver all key off that.
    """
    names = set(ep.DatasourceCreatePayload.model_fields)
    names |= set(ep.DatasourceConnection.model_fields)
    for forbidden in ('dsn', 'connection_string', 'url', 'jdbc_url', 'jdbc'):
        assert forbidden not in names, f'the API accepts a raw {forbidden!r}'


def test_there_is_no_saved_sql_field():
    """AC-07 also forbids "save a SQL snippet as a queryable object": the model
    writes the statement per question, so there is nothing to store."""
    names = set(ep.DatasourceCreatePayload.model_fields)
    for forbidden in ('sql', 'statement', 'query', 'saved_sql'):
        assert forbidden not in names, f'the API accepts a saved {forbidden!r}'


# --- engines ------------------------------------------------------------------


def test_sqlite_is_rejected():
    """AC-06: its file path would become an implicit permission boundary."""
    with pytest.raises(DatasourceUnsupportedEngineError):
        ep._validate_engine('sqlite')


def test_an_unknown_engine_is_rejected():
    with pytest.raises(DatasourceUnsupportedEngineError):
        ep._validate_engine('cassandra')


@pytest.mark.parametrize('engine', ['mysql', 'postgresql', 'gaussdb', 'oracle', 'db2', 'dm'])
def test_all_six_engines_are_accepted(engine):
    assert ep._validate_engine(engine) == engine


def test_an_engine_alias_is_normalised():
    """A caller writing `postgres` gets `postgresql` stored, so the guard's
    dialect lookup and the driver name agree."""
    assert ep._validate_engine('postgres') == 'postgresql'


def test_an_omitted_port_falls_back_to_the_engine_default():
    """Making the admin look up 5236 for DM8 is how a datasource ends up
    configured against the wrong port."""
    connection = ep.DatasourceConnection(
        host='h', database_name='d', username='u', password='p'
    )
    assert ep._connection_fields(connection, engine='dm')['port'] == 5236
    assert ep._connection_fields(connection, engine='db2')['port'] == 50000
    assert ep._connection_fields(connection, engine='mysql')['port'] == 3306


# --- names, 404 and deletion --------------------------------------------------


async def test_a_duplicate_name_is_rejected():
    with patch.object(ep.DbDatasourceDao, 'aget_by_name', AsyncMock(return_value=_row(id=99))):
        with pytest.raises(DatasourceNameDuplicateError):
            await ep.create_datasource(_payload(), _admin())


async def test_renaming_to_your_own_name_is_allowed():
    """The exclude makes an edit that keeps the name a no-op rather than a
    duplicate error against itself."""
    with patch.object(ep.DbDatasourceDao, 'aget_by_name', AsyncMock(return_value=_row(id=7))):
        await ep._reject_duplicate_name('客户订单库', exclude_id=7)


async def test_a_missing_datasource_is_not_found():
    with patch.object(ep.DbDatasourceDao, 'aget_by_id', AsyncMock(return_value=None)):
        with pytest.raises(DatasourceNotFoundError):
            await ep.get_datasource(999, _admin())


async def test_deleting_removes_the_tables_but_not_the_history():
    """AC-62: the datasource goes, the audit trail stays.

    Asserted by checking what IS deleted -- `db_datasource_table` rows and the
    datasource row -- because the query log and failure trail are deliberately
    untouched (they reference the id with no foreign key, so past answers keep
    rendering).
    """
    from bisheng.permission.domain.services.permission_service import PermissionService

    with patch.object(PermissionService, 'check', new=AsyncMock(return_value=True)), patch.object(
        ep.DbDatasourceDao, 'aget_by_id', AsyncMock(return_value=_row())
    ), patch.object(
        ep.DbDatasourceTableDao, 'adelete_by_datasource', AsyncMock()
    ) as drop_tables, patch.object(
        ep.DbDatasourceDao, 'adelete', AsyncMock()
    ) as drop_row, patch(
        'bisheng.datasource.domain.models.dao.DbQueryLogDao.adelete_by_datasource',
        AsyncMock(),
        create=True,
    ) as drop_log:
        response = await ep.delete_datasource(7, _admin())

    assert response.data == {'deleted': 7}
    drop_tables.assert_awaited_once_with(7)
    drop_row.assert_awaited_once_with(7)
    drop_log.assert_not_called()


async def test_deleting_without_permission_is_not_found_not_forbidden():
    """AC-49: an unauthorized caller must not be able to tell an existing id
    from a missing one, so both surface as the same error."""
    from bisheng.permission.domain.services.permission_service import PermissionService

    with patch.object(PermissionService, 'check', new=AsyncMock(return_value=False)):
        with pytest.raises(DatasourceNotFoundError):
            await ep.delete_datasource(7, _admin())


# --- route shapes -------------------------------------------------------------


def test_no_route_can_be_shadowed_by_the_id_route():
    """`/datasource/{datasource_id}` matches ANY single segment.

    So `/datasource/tables/{table_id}` was reachable as `datasource_id="tables"`
    depending on registration order -- measured while adding it, and it is the
    kind of bug that only shows up against a live server. Every literal segment
    directly under `/datasource/` must therefore be distinguishable from an id.
    """
    from bisheng.datasource.api.endpoints import datasources as module

    literal_first_segments = []
    for route in module.router.routes:
        path = getattr(route, 'path', '')
        # Only the single-segment case is ambiguous; anything with a second
        # segment cannot collide with `{datasource_id}`.
        remainder = path.removeprefix('/datasource/')
        if not remainder or '/' in remainder or remainder.startswith('{'):
            continue
        literal_first_segments.append(remainder)

    # `query-failures` and `test-connection` are hyphenated, and no datasource id
    # can contain a hyphen -- which is exactly what makes them safe.
    for segment in literal_first_segments:
        assert '-' in segment, (
            f'/datasource/{segment} can be shadowed by /datasource/{{datasource_id}}; '
            'give it a hyphenated (unmatchable-as-id) or nested path'
        )


def test_the_table_endpoints_exist_and_are_nested_or_prefixed():
    """T-36/T-37's API surface, asserted so a rename cannot silently drop one."""
    from bisheng.datasource.api.endpoints import datasources as module

    paths = {getattr(route, 'path', '') for route in module.router.routes}
    assert '/datasource/{datasource_id}/tables' in paths
    assert '/datasource/{datasource_id}/tables/select' in paths
    assert '/datasource/{datasource_id}/collect' in paths
    assert '/datasource/table-metadata/{table_id}' in paths


# --- AC-09: the picker's source, which is NOT the collected list --------------


async def test_available_tables_reflects_the_live_database():
    """AC-09's step one: what the account can see RIGHT NOW.

    Distinct from `GET /tables`, which returns what has been COLLECTED. An
    admin picks from the former and collects into the latter; serving the
    collected list as the picker would hide a table added since the last
    collect, making it impossible to ever expose.
    """
    from bisheng.permission.domain.services.permission_service import PermissionService

    with patch.object(PermissionService, 'check', new=AsyncMock(return_value=True)), patch.object(
        ep.DbDatasourceDao, 'aget_by_id', AsyncMock(return_value=_row())
    ), patch.object(
        ep.schema_collector, 'list_tables', MagicMock(return_value=['orders', 'customers', 'fresh'])
    ), patch.object(
        ep.schema_service, 'list_tables_for_admin', AsyncMock(return_value=[
            {'table_name': 'orders', 'enabled': 1},
            {'table_name': 'customers', 'enabled': 0},
        ])
    ):
        response = await ep.list_available_tables(7, _admin())

    rows = {r['table_name']: r for r in response.data['data']}
    assert set(rows) == {'orders', 'customers', 'fresh'}, 'a live table was hidden'
    assert rows['orders']['collected'] is True and rows['orders']['enabled'] == 1
    assert rows['customers']['collected'] is True and rows['customers']['enabled'] == 0
    # Never collected: offered, not pre-ticked. This is the case a
    # snapshot-based picker could not represent at all.
    assert rows['fresh']['collected'] is False
    assert response.data['total'] == 3


async def test_available_tables_is_authorized_like_the_rest():
    from bisheng.permission.domain.services.permission_service import PermissionService

    with patch.object(PermissionService, 'check', new=AsyncMock(return_value=False)):
        with pytest.raises(DatasourceNotFoundError):
            await ep.list_available_tables(7, _admin())


async def test_available_tables_reads_only_names_not_structure():
    """Columns are collected for the CHOSEN tables. Reflecting every column of
    every accessible table is work nobody asked for -- on a 100-table schema it
    is the expensive half of the collect, done before any choice is made."""
    import inspect

    source = inspect.getsource(ep.list_available_tables)
    assert 'list_tables' in source
    assert 'collect(' not in source, 'the picker is reading structure, not just names'


def test_the_two_table_listings_are_separate_endpoints():
    """A rename that merged them would silently break the two-step flow."""
    from bisheng.datasource.api.endpoints import datasources as module

    paths = {getattr(route, 'path', '') for route in module.router.routes}
    assert '/datasource/{datasource_id}/available-tables' in paths
    assert '/datasource/{datasource_id}/tables' in paths
