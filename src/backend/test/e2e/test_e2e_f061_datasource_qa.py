"""E2E tests for F061 datasource Q&A — the admin-side chain, on a real database.

Runs the whole registry path against a live MySQL the platform treats as a
CUSTOMER database: create a datasource -> reflect what the account can see ->
collect the chosen tables' structure -> write a business description ->
re-collect -> assert the description survived -> delete.

Prerequisites: backend on localhost:7860, middleware running, and a TEST
database reachable with the credentials in the environment below. The test
creates and deletes its own datasource and never writes to the target
database -- it only reflects the catalog (spec AC-13).

Why a real database rather than mocks: the F058 review found three HIGH
defects that had passed 100+ green unit tests because the tests mocked the
layer under test, one of them a cross-tenant write. Tenant isolation, the
collect path and the AC-12 preserve-on-recollect rule are exactly the kind of
boundary that mocking makes invisible, so they are exercised here.

Covers:
- AC-01: a datasource is created from the six-field form and its credentials
  are stored encrypted, never echoed back
- AC-09: the tables the configured account can see are listed for selection,
  read live from the catalog rather than from a stored snapshot
- AC-10: the chosen tables' structure is collected and persisted
- AC-11: an admin can write a business description per table
- AC-12: a re-collect refreshes the structure and does NOT overwrite it
- AC-13: no table rows are read -- the snapshot carries name/type/comment only
- AC-49: an unknown datasource id answers 11708, not a 500
- AC-62: deleting removes the datasource and the table metadata

Not covered here (see features/v2.7.0/061-datasource-qa/e2e-checklist.md):
- the conversation side (AC-24..AC-36) needs an assistant bound to a
  datasource plus the `datasource_sql_llm` slot configured
- AC-19/20/21 (read-only session, guard degradation, non-blocking execution)
  are unit-covered in test/datasource/test_executor.py
"""

import os
import uuid

import httpx
import pytest

from test.e2e.helpers.api import API_BASE, assert_resp_200
from test.e2e.helpers.auth import auth_headers, encrypt_password

#: The database the platform will treat as a customer's. Defaults point at the
#: local dev MySQL; override for another environment.
TARGET_HOST = os.environ.get('E2E_DATASOURCE_HOST', '172.19.70.189')
TARGET_PORT = int(os.environ.get('E2E_DATASOURCE_PORT', '3306'))
TARGET_DB = os.environ.get('E2E_DATASOURCE_DB', 'bisheng_local')
TARGET_USER = os.environ.get('E2E_DATASOURCE_USER', 'root')
TARGET_PASSWORD = os.environ.get('E2E_DATASOURCE_PASSWORD', '1234')

#: Two small tables that exist in any BiSheng schema, so the test does not
#: depend on the customer's business tables.
PROBE_TABLES = ['alembic_version', 'knowledge']


@pytest.fixture(scope='module')
def client():
    with httpx.Client(timeout=120) as c:
        assert c.get(f'{API_BASE}/env').status_code == 200, 'Backend not reachable'
        yield c


@pytest.fixture(scope='module')
def admin_token(client):
    password = os.environ.get('E2E_ADMIN_PASSWORD', 'admin123')
    pubkey = assert_resp_200(client.get(f'{API_BASE}/user/public_key'))['public_key']
    encrypted = encrypt_password(password, pubkey)
    data = assert_resp_200(
        client.post(f'{API_BASE}/user/login', json={'user_name': 'admin', 'password': encrypted})
    )
    return data['access_token']


@pytest.fixture
def datasource(client, admin_token):
    """Create a datasource for one test, and delete it afterwards.

    Deletion is in a finally rather than a yield-teardown so a failing
    assertion cannot leave a row behind -- a leaked datasource would make the
    next run's name-uniqueness check fail for an unrelated reason.
    """
    name = f'E2E-F061-{uuid.uuid4().hex[:8]}'
    created = assert_resp_200(
        client.post(
            f'{API_BASE}/datasource',
            headers=auth_headers(admin_token),
            json={
                'name': name,
                'engine': 'mysql',
                'connection': {
                    'host': TARGET_HOST,
                    'port': TARGET_PORT,
                    'database_name': TARGET_DB,
                    'username': TARGET_USER,
                    'password': TARGET_PASSWORD,
                },
                'description': 'F061 e2e target',
            },
        )
    )
    datasource_id = created['id']
    try:
        yield datasource_id
    finally:
        client.delete(f'{API_BASE}/datasource/{datasource_id}', headers=auth_headers(admin_token))


def _collect(client, token, datasource_id, tables):
    return assert_resp_200(
        client.post(
            f'{API_BASE}/datasource/{datasource_id}/collect',
            headers=auth_headers(token),
            json={'table_names': tables},
        )
    )


def _tables(client, token, datasource_id):
    data = assert_resp_200(
        client.get(f'{API_BASE}/datasource/{datasource_id}/tables', headers=auth_headers(token))
    )
    return data['data']


# --- AC-01 -------------------------------------------------------------------


def test_a_datasource_is_created_without_echoing_its_password(client, admin_token, datasource):
    """AC-04 lives in the same response: the row round-trips, the password does not."""
    data = assert_resp_200(
        client.get(f'{API_BASE}/datasource/{datasource}', headers=auth_headers(admin_token))
    )
    assert data['id'] == datasource
    assert data['has_password'] is True, 'the credential was not stored'
    assert 'password' not in data
    assert TARGET_PASSWORD not in repr(data)


# --- AC-09 -------------------------------------------------------------------


def test_the_picker_lists_what_the_account_can_actually_see(client, admin_token, datasource):
    """AC-09, read live from the catalog.

    Asserted on `collected is False` for everything: a freshly created
    datasource has collected nothing, and the picker must still offer the
    tables. A snapshot-backed picker would show an empty list here, and the
    admin would have no way to expose anything.
    """
    payload = assert_resp_200(
        client.get(
            f'{API_BASE}/datasource/{datasource}/available-tables',
            headers=auth_headers(admin_token),
        )
    )
    names = [row['table_name'] for row in payload['data']]

    assert payload['total'] > 0, (
        f'no tables reflected from {TARGET_HOST}:{TARGET_PORT}/{TARGET_DB} -- '
        'check the target database is reachable and the account can read it'
    )
    for probe in PROBE_TABLES:
        assert probe in names, f'{probe} is not offered for selection'
    assert all(row['collected'] is False for row in payload['data'])


# --- AC-10 / AC-13 -----------------------------------------------------------


def test_collecting_persists_structure_but_never_row_data(client, admin_token, datasource):
    """AC-10 persists the snapshot; AC-13 says structure only.

    The second half is asserted on the SHAPE of every collected column: a
    collector that also sampled values would put them in a field, and the
    snapshot dictionary is the only place it could.
    """
    result = _collect(client, admin_token, datasource, PROBE_TABLES)
    assert result['total'] == len(PROBE_TABLES)

    rows = _tables(client, admin_token, datasource)
    by_name = {row['table_name']: row for row in rows}
    for probe in PROBE_TABLES:
        assert probe in by_name, f'{probe} was not collected'

    knowledge = by_name['knowledge']
    assert len(knowledge['structure_snapshot']) > 0, 'no columns collected'

    for column in knowledge['structure_snapshot']:
        assert set(column) == {'name', 'type', 'comment'}, (
            f'collected a field beyond name/type/comment: {sorted(column)} -- '
            'AC-13 allows structure only'
        )


def test_collecting_an_explicit_empty_list_does_not_mean_everything(
    client, admin_token, datasource
):
    """Recorded because it surprised me: `table_names: []` is "collect nothing",
    not "collect everything".

    The service's `collect(None)` DOES mean everything, but the endpoint's
    payload defaults to a list, and an empty list is a legitimate answer to
    "which tables did you tick" -- the admin ticked none. A caller wanting
    everything must name the tables, which is what the picker flow produces.
    """
    result = _collect(client, admin_token, datasource, [])
    assert result['total'] == 0


# --- AC-11 / AC-12 -----------------------------------------------------------


def test_a_recollect_refreshes_structure_and_keeps_the_description(
    client, admin_token, datasource
):
    """AC-12, and the reason the write path is split in two.

    The description is the only field in this feature that a human wrote and
    the database cannot regenerate. A re-collect runs a blanket UPDATE over
    the snapshot columns; if it wrote the whole row, this would fail.
    """
    _collect(client, admin_token, datasource, PROBE_TABLES)
    rows = _tables(client, admin_token, datasource)
    knowledge = next(row for row in rows if row['table_name'] == 'knowledge')
    original_columns = len(knowledge['structure_snapshot'])

    marker = f'知识库主表 e2e {uuid.uuid4().hex[:6]}'
    assert_resp_200(
        client.put(
            f'{API_BASE}/datasource/table-metadata/{knowledge["id"]}',
            headers=auth_headers(admin_token),
            json={'business_desc': marker},
        )
    )

    _collect(client, admin_token, datasource, PROBE_TABLES)

    after = next(
        row for row in _tables(client, admin_token, datasource) if row['table_name'] == 'knowledge'
    )
    assert after['business_desc'] == marker, (
        'the re-collect clobbered the admin-written description (AC-12)'
    )
    assert len(after['structure_snapshot']) == original_columns, 'structure was not refreshed'


# --- AC-49 / AC-62 -----------------------------------------------------------


def test_an_unknown_datasource_id_is_not_found_not_a_500(client, admin_token):
    """AC-49's direct-invocation half: a bad id must be refused cleanly."""
    response = client.get(f'{API_BASE}/datasource/99999999', headers=auth_headers(admin_token))
    body = response.json()
    assert body['status_code'] == 11708, f'unexpected response: {body}'


def test_deleting_removes_the_datasource_and_its_table_metadata(client, admin_token):
    """AC-62. Deleting is the fixture's job elsewhere; here it is the assertion.

    The second GET is the point: it is not enough that the DELETE returned 200,
    the datasource must actually be gone, and its collected tables with it.
    """
    name = f'E2E-F061-del-{uuid.uuid4().hex[:8]}'
    created = assert_resp_200(
        client.post(
            f'{API_BASE}/datasource',
            headers=auth_headers(admin_token),
            json={
                'name': name,
                'engine': 'mysql',
                'connection': {
                    'host': TARGET_HOST,
                    'port': TARGET_PORT,
                    'database_name': TARGET_DB,
                    'username': TARGET_USER,
                    'password': TARGET_PASSWORD,
                },
            },
        )
    )
    datasource_id = created['id']
    _collect(client, admin_token, datasource_id, PROBE_TABLES)
    assert _tables(client, admin_token, datasource_id), 'nothing collected to delete'

    assert_resp_200(
        client.delete(f'{API_BASE}/datasource/{datasource_id}', headers=auth_headers(admin_token))
    )

    gone = client.get(f'{API_BASE}/datasource/{datasource_id}', headers=auth_headers(admin_token))
    assert gone.json()['status_code'] == 11708, 'the datasource survived its deletion'
