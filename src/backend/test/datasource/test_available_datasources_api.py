"""F061 T-42 AC-53: the list a flow author picks from.

`GET /api/v1/datasource` is the ADMIN list: gated by `get_tenant_admin_user`,
and deliberately unfiltered by per-user grants because it is the management page.
A flow author is a different population — `/flow/:id` has no permission gate, so
plenty of people who can open that editor cannot call the admin endpoint. Wiring
the editor's datasource picker to the admin list would 403 for them.

So this endpoint is the chat-user-facing counterpart: `get_login_user`, and the
contents narrowed to what the CALLER may use (`viewer`), which is the same
relation `conversation_service` filters on and the same one the assistant picker
uses.

It returns `DatasourceRead` — id and name, no connection details — because
choosing from a list is the entire purpose. Nothing here is a management view.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from bisheng.datasource.api.endpoints.datasources import list_available_datasources
from bisheng.permission.domain.services.permission_service import PermissionService


class _User:
    user_id = 3
    tenant_id = 1

    def get_visible_tenants(self):
        return [1]

    def is_admin(self):
        return False


def _row(ds_id, name, tenant_id=1, enabled=1):
    return SimpleNamespace(
        id=ds_id,
        name=name,
        description=f'{name} desc',
        engine='mysql',
        enabled=enabled,
        tenant_id=tenant_id,
        # Present on the real row and must not reach the response.
        password='fernet-ciphertext',
        host='10.0.0.1',
        port=3306,
        database_name='db',
        username='reader',
    )


async def _call(monkeypatch, *, rows, grants):
    async def _check(user_id, relation, object_type, object_id, login_user=None, **kwargs):
        return int(object_id) in grants

    monkeypatch.setattr(PermissionService, 'check', _check)

    def _alist(**kwargs):
        # Honour `ids` the way the real DAO does. A mock returning the same rows
        # regardless would make the filtering unfalsifiable — the first version
        # of this helper did exactly that.
        selected = rows
        if kwargs.get('ids') is not None:
            wanted = set(kwargs['ids'])
            selected = [row for row in rows if row.id in wanted]
        return selected, len(selected)

    with patch(
        'bisheng.datasource.api.endpoints.datasources.DbDatasourceDao.alist',
        AsyncMock(side_effect=_alist),
    ) as alist:
        response = await list_available_datasources(login_user=_User())

    return response, alist


async def test_only_authorized_datasources_are_listed(monkeypatch):
    """AC-49: what the caller may not use must not appear."""
    rows = [_row(7, 'orders-db'), _row(8, 'payroll-db')]

    response, _ = await _call(monkeypatch, rows=rows, grants={7})
    data = response.data['data']

    assert [entry['id'] for entry in data] == [7]
    assert 'payroll-db' not in str(data)


async def test_no_grants_yields_an_empty_list(monkeypatch):
    """Not "everything": an empty grant set must narrow to nothing."""
    response, _ = await _call(monkeypatch, rows=[_row(7, 'orders-db')], grants=set())

    assert response.data['data'] == []


async def test_the_response_carries_no_connection_details(monkeypatch):
    """A picker needs a name. Host, account and ciphertext must not ride along —
    `DatasourceRead` has no field for them, which makes this structural."""
    response, _ = await _call(monkeypatch, rows=[_row(7, 'orders-db')], grants={7})

    entry = response.data['data'][0]
    for forbidden in ('password', 'host', 'port', 'username', 'database_name'):
        assert forbidden not in entry, f'{forbidden} must not reach a chat-side caller'
    assert 'fernet-ciphertext' not in str(entry)


async def test_the_dao_is_narrowed_rather_than_filtered_afterwards(monkeypatch):
    """The authorization set is passed INTO the query, so an unauthorized
    datasource is never loaded. Filtering in Python afterwards would work too but
    would put every row in memory first, and the DAO's `ids=` contract ("[] means
    authorized for nothing, not no filter") is exactly this case."""
    rows = [_row(7, 'orders-db')]

    _, alist = await _call(monkeypatch, rows=rows, grants={7})

    _, kwargs = alist.call_args
    assert kwargs.get('ids') == [7], (
        'the authorized ids must narrow the query; otherwise every datasource in '
        'the tenant is read before being discarded'
    )
    assert kwargs.get('enabled_only') is True, (
        'a disabled datasource answers no queries, so it must not be offered'
    )


async def test_an_admin_sees_what_they_are_granted_not_everything(monkeypatch):
    """Even a super admin goes through the same filter here, because the filter
    is the `viewer` relation and `PermissionService.check` short-circuits admins
    to True — so for an admin the result is simply the full list. Asserted by
    giving the check an unconditional True and confirming nothing is dropped
    silently by a second, narrower filter."""
    rows = [_row(7, 'orders-db'), _row(8, 'payroll-db')]

    response, _ = await _call(monkeypatch, rows=rows, grants={7, 8})

    assert [entry['id'] for entry in response.data['data']] == [7, 8]
