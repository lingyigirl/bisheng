"""F064 e2e: a field correction survives a re-collect, and never leaves the platform.

Real backend on localhost:7860, the shared middleware, and a real database for
the platform to treat as a customer's (the same target F061's suite uses; see
the env vars below).

WHAT THIS SUITE IS FOR
----------------------
The unit tests pin the rules: `render_table` prefers the override, the write
path normalises blanks away, a re-collect never names the new column. What they
cannot pin is the pair of promises the feature actually makes to an admin:

  1. **The correction outlives a re-collect.** `areplace_snapshot` replaces the
     whole structure snapshot on every collect, so "the override survives" is a
     statement about two code paths agreeing — and only a real collect runs the
     real collector, the real DAO and the real column.
  2. **The correction never reaches the customer's database.** INV-19 clause 2
     says the query path admits single SELECTs only, so the platform has no
     write channel at all; this suite checks the consequence directly, by
     reading the customer database's own column comments before and after and
     requiring them to be byte-identical. That is the one thing no mock can
     demonstrate, and it is the promise the whole design rests on.

WHAT IT DOES NOT COVER
----------------------
**AC-06 (the injected text) is not observable over HTTP.** Nothing in a
response carries the schema block the model is handed, so "the prompt uses the
correction" stays a unit-test claim (`test_schema_prompt.py`, mutation-checked).
Widening this suite to a real model turn would assert on the model's phrasing,
which F061's suite records as a bad trade.

AC-05 (a correction whose column is gone) IS covered here, because it is a
statement about the stored row rather than about the prompt.
"""

import os
import uuid
from collections.abc import Iterator

import httpx
import pytest

from test.e2e.helpers.api import API_BASE, assert_resp_200
from test.e2e.helpers.auth import auth_headers, encrypt_password

#: The database the platform will treat as a customer's. Same defaults as F061's
#: suite -- it is the dev MySQL, and the point is that the platform must not
#: write to it, so using a real one is the whole test.
TARGET_HOST = os.environ.get('E2E_DATASOURCE_HOST', '172.19.70.189')
TARGET_PORT = int(os.environ.get('E2E_DATASOURCE_PORT', '3306'))
TARGET_DB = os.environ.get('E2E_DATASOURCE_DB', 'bisheng_local')
TARGET_USER = os.environ.get('E2E_DATASOURCE_USER', 'root')
TARGET_PASSWORD = os.environ.get('E2E_DATASOURCE_PASSWORD', '1234')

#: A table that exists in any BiSheng schema, with a column we can name a
#: correction for. Not a business table: the suite must not depend on the
#: customer's data model.
PROBE_TABLE = 'alembic_version'
PROBE_COLUMN = 'version_num'


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
def datasource(client, admin_token) -> Iterator[int]:
    """A datasource for one test, deleted afterwards.

    Deletion sits in a `finally` so a failing assertion cannot leave the row
    behind -- a leaked datasource makes the next run fail on name uniqueness,
    for a reason unrelated to what it was testing.
    """
    name = f'E2E-F064-{uuid.uuid4().hex[:8]}'
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
                'description': 'F064 e2e target',
            },
        )
    )
    datasource_id = created['id']
    try:
        yield datasource_id
    finally:
        client.delete(f'{API_BASE}/datasource/{datasource_id}', headers=auth_headers(admin_token))


def _collect(client, token, datasource_id) -> dict:
    return assert_resp_200(
        client.post(
            f'{API_BASE}/datasource/{datasource_id}/collect',
            headers=auth_headers(token),
            json={'table_names': [PROBE_TABLE]},
        )
    )


def _tables(client, token, datasource_id) -> list[dict]:
    data = assert_resp_200(
        client.get(f'{API_BASE}/datasource/{datasource_id}/tables', headers=auth_headers(token))
    )
    return data['data']


def _put_metadata(client, token, table_id: int, body: dict) -> dict:
    return assert_resp_200(
        client.put(
            f'{API_BASE}/datasource/table-metadata/{table_id}',
            headers=auth_headers(token),
            json=body,
        )
    )


def _customer_column_comments() -> dict[str, str | None]:
    """The customer database's OWN column comments, read directly.

    Read over a separate connection rather than through the platform: the claim
    under test is that the platform never wrote here, and asking the platform
    would be asking the suspect.
    """
    from sqlalchemy import create_engine, text

    url = (
        f'mysql+pymysql://{TARGET_USER}:{TARGET_PASSWORD}'
        f'@{TARGET_HOST}:{TARGET_PORT}/{TARGET_DB}'
    )
    engine = create_engine(url)
    try:
        with engine.connect() as conn:
            rows = conn.execute(
                text(
                    'SELECT column_name, column_comment FROM information_schema.columns '
                    'WHERE table_schema = :db AND table_name = :tbl'
                ),
                {'db': TARGET_DB, 'tbl': PROBE_TABLE},
            ).all()
            return dict(rows)
    finally:
        engine.dispose()


def _override_of(row: dict, column: str) -> str | None:
    return (row.get('column_overrides') or {}).get(column)


# --- AC-02 / AC-04: the correction outlives a re-collect ---------------------


def test_an_override_survives_a_recollect(client, admin_token, datasource):
    """The feature's central promise, run against a real collector.

    A re-collect REPLACES the structure snapshot wholesale. The override lives
    in its own column precisely so that is harmless, and this is the only test
    that runs the real `refresh_tables` against a real database to find out.
    """
    _collect(client, admin_token, datasource)
    row = _tables(client, admin_token, datasource)[0]
    assert row['table_name'] == PROBE_TABLE
    assert any(c['name'] == PROBE_COLUMN for c in row['structure_snapshot']), (
        f'{PROBE_TABLE} has no {PROBE_COLUMN} column in this deployment'
    )

    _put_metadata(
        client,
        admin_token,
        row['id'],
        {'column_overrides': {PROBE_COLUMN: 'F064 e2e correction'}},
    )
    assert _override_of(_tables(client, admin_token, datasource)[0], PROBE_COLUMN) == (
        'F064 e2e correction'
    )

    _collect(client, admin_token, datasource)

    after = _tables(client, admin_token, datasource)[0]
    assert _override_of(after, PROBE_COLUMN) == 'F064 e2e correction', (
        'the re-collect discarded the admin correction -- AC-04 is exactly this'
    )
    # Constructive guard: the collect really did run, so the assertion above is
    # not passing because nothing happened.
    assert after['structure_snapshot'], 'the re-collect left no structure at all'


def test_a_recollect_never_touches_the_customers_database(client, admin_token, datasource):
    """AC-02, checked at the only place that can prove it: the customer's side.

    INV-19 clause 2 gives the platform no write channel, so this cannot fail
    through a bug in F064 alone -- which is precisely why it is asserted. If a
    future change ever opened a write path (an `ALTER`, a `COMMENT ON`, a
    "helpful" sync-back), the correction would silently start overwriting the
    customer's own documentation, and nothing else in the suite would notice.
    """
    before = _customer_column_comments()
    assert before, f'could not read {PROBE_TABLE} comments from {TARGET_DB}'

    _collect(client, admin_token, datasource)
    row = _tables(client, admin_token, datasource)[0]
    _put_metadata(
        client,
        admin_token,
        row['id'],
        {'column_overrides': {PROBE_COLUMN: 'F064 e2e correction'}},
    )
    _collect(client, admin_token, datasource)

    after = _customer_column_comments()
    assert after == before, (
        'the customer database column comments changed; the platform is supposed '
        f'to have no write channel to it (INV-19 clause 2). diff='
        f'{ {k: (before.get(k), after.get(k)) for k in set(before) | set(after) if before.get(k) != after.get(k)} }'
    )


# --- AC-01: compatibility, end to end ---------------------------------------


def test_saving_only_the_description_keeps_the_corrections(client, admin_token, datasource):
    """The regression the `model_fields_set` rule exists to prevent.

    Existing clients post `business_desc` alone and are NOT upgraded with the
    server. Run over HTTP so the presence rule is exercised where it actually
    lives -- in the request body, not in a Python call signature.
    """
    _collect(client, admin_token, datasource)
    row = _tables(client, admin_token, datasource)[0]
    _put_metadata(
        client, admin_token, row['id'], {'column_overrides': {PROBE_COLUMN: 'keep me'}}
    )

    _put_metadata(client, admin_token, row['id'], {'business_desc': 'F064 e2e description'})

    after = _tables(client, admin_token, datasource)[0]
    assert after['business_desc'] == 'F064 e2e description'
    assert _override_of(after, PROBE_COLUMN) == 'keep me', (
        'a save that never mentioned the corrections cleared them'
    )


def test_an_explicit_null_clears_the_corrections(client, admin_token, datasource):
    """The undo path: `null` is a value, so it has to survive the round trip."""
    _collect(client, admin_token, datasource)
    row = _tables(client, admin_token, datasource)[0]
    _put_metadata(
        client, admin_token, row['id'], {'column_overrides': {PROBE_COLUMN: 'remove me'}}
    )

    _put_metadata(client, admin_token, row['id'], {'column_overrides': None})

    assert _override_of(_tables(client, admin_token, datasource)[0], PROBE_COLUMN) is None


def test_blank_corrections_are_stored_as_nothing_rather_than_as_empty_strings(
    client, admin_token, datasource
):
    """决策 5 at the boundary where it matters: the request body.

    An empty string would be a third state ("corrected, to nothing") that the
    UI and the injection path would each have to agree about; AC-09 admits two.
    """
    _collect(client, admin_token, datasource)
    row = _tables(client, admin_token, datasource)[0]

    _put_metadata(client, admin_token, row['id'], {'column_overrides': {PROBE_COLUMN: '   '}})

    stored = _tables(client, admin_token, datasource)[0].get('column_overrides')
    assert stored == {}, f'an all-blank submission stored {stored!r}'


# --- AC-05: a correction whose column is gone -------------------------------


def test_a_correction_for_a_missing_column_is_kept_not_dropped(client, admin_token, datasource):
    """A rename must not silently delete the admin's note.

    The column never existed here, which is the same stored state a rename
    produces: a key with no matching column in the snapshot.
    """
    _collect(client, admin_token, datasource)
    row = _tables(client, admin_token, datasource)[0]

    _put_metadata(
        client,
        admin_token,
        row['id'],
        {'column_overrides': {PROBE_COLUMN: 'live note', 'no_such_column': 'orphan note'}},
    )

    stored = _tables(client, admin_token, datasource)[0]['column_overrides']
    assert stored.get('no_such_column') == 'orphan note', (
        f'the correction for a column the snapshot does not have was dropped: {stored!r}'
    )
    assert stored.get(PROBE_COLUMN) == 'live note'
