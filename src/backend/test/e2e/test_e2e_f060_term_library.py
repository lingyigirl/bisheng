"""F060 e2e: the term library over a real backend (spec AC-01 ~ AC-13).

Real backend + middleware required (localhost:7860, override via E2E_API_BASE).

What is covered here is everything reachable over HTTP: CRUD, the validation
rules, both import modes, and the tenant scoping the DAO enforces. What is NOT
here — and cannot be — is the claim the whole feature exists for:

  **AC-25: a term edited mid-session changes the NEXT turn's answer.**

That needs a real model in the loop, so it is a manual checklist item
(`features/v2.7.0/060-term-library/e2e-checklist.md`). `test_injection_sites.py`
approximates it at the unit level by asserting the prompt the MODEL receives
changes between turns on the same agent object, but only a real conversation
proves the answer changes.

The suite is self-cleaning: every term it creates is deleted in teardown, and
the OVERWRITE test runs LAST-restorable — it deliberately empties the library,
so it re-adds what it removed. Leaving a shared dev tenant's term library
emptied would be the harmful outcome.

Chinese strings here are the terms under test, and the assertions compare them
byte-for-byte; the fullwidth punctuation is deliberate.
"""

import os
import uuid

import httpx
import pytest

from test.e2e.helpers.api import API_BASE, assert_resp_200
from test.e2e.helpers.auth import auth_headers, encrypt_password

TERMS_URL = f'{API_BASE}/term-library'

# Unique per run so a re-run never collides with terms an earlier run left behind.
TERM_A = f'F060甲{uuid.uuid4().hex[:8]}'
TERM_B = f'F060乙{uuid.uuid4().hex[:8]}'
ALIAS_A = f'甲别{uuid.uuid4().hex[:6]}'


@pytest.fixture(scope="module")
def client():
    with httpx.Client(timeout=60) as c:
        assert c.get(f'{API_BASE}/env').status_code == 200, 'Backend not reachable'
        yield c


def _login(client: httpx.Client, username: str, password: str) -> str:
    """Sync RSA login (helpers/auth.py is async-only; mirror its flow)."""
    pubkey = assert_resp_200(client.get(f'{API_BASE}/user/public_key'))['public_key']
    encrypted = encrypt_password(password, pubkey)
    data = assert_resp_200(
        client.post(f'{API_BASE}/user/login', json={'user_name': username, 'password': encrypted})
    )
    return data['access_token']


@pytest.fixture(scope="module")
def admin_token(client):
    return _login(client, 'admin', os.environ.get('E2E_ADMIN_PASSWORD', 'admin123'))


@pytest.fixture(scope="module")
def created_term_ids():
    ids: list[int] = []
    yield ids


@pytest.fixture(scope="module", autouse=True)
def _cleanup(client, admin_token, created_term_ids):
    """Delete everything this run created. Best-effort, never re-raises."""
    yield
    for term_id in created_term_ids:
        try:
            client.delete(f'{TERMS_URL}/{term_id}', headers=auth_headers(admin_token))
        except Exception:
            pass


def _create(client, token, *, standard_term, definition, aliases=None, department_id=None):
    return client.post(
        TERMS_URL,
        json={
            'standard_term': standard_term,
            'definition': definition,
            'aliases': aliases or [],
            'department_id': department_id,
        },
        headers=auth_headers(token),
    )


# ---------------------------------------------------------------------------
# CRUD and the validation rules (AC-01 ~ AC-04)
# ---------------------------------------------------------------------------


def test_create_list_update_delete_a_term(client, admin_token, created_term_ids):
    """AC-01: 增删改查的完整往返."""
    created = assert_resp_200(
        _create(client, admin_token, standard_term=TERM_A, definition='释义甲', aliases=[ALIAS_A])
    )
    created_term_ids.append(created['id'])
    assert created['standard_term'] == TERM_A
    assert created['aliases'] == [ALIAS_A]

    listed = assert_resp_200(
        client.get(TERMS_URL, params={'page': 1, 'page_size': 100}, headers=auth_headers(admin_token))
    )
    assert any(row['id'] == created['id'] for row in listed['data'])

    updated = assert_resp_200(
        client.put(
            f'{TERMS_URL}/{created["id"]}',
            json={
                'standard_term': TERM_A,
                'definition': '改后的释义',
                'aliases': [ALIAS_A],
                'department_id': None,
            },
            headers=auth_headers(admin_token),
        )
    )
    assert updated['definition'] == '改后的释义'

    assert_resp_200(client.delete(f'{TERMS_URL}/{created["id"]}', headers=auth_headers(admin_token)))
    created_term_ids.remove(created['id'])

    after = assert_resp_200(
        client.get(TERMS_URL, params={'page': 1, 'page_size': 100}, headers=auth_headers(admin_token))
    )
    assert all(row['id'] != created['id'] for row in after['data'])


def test_a_blank_standard_term_is_rejected(client, admin_token):
    """AC-04, as 11502 rather than a 500."""
    resp = _create(client, admin_token, standard_term='   ', definition='释义')
    assert resp.status_code >= 400
    assert resp.json().get('status_code') == 11502, resp.text[:200]


def test_a_blank_definition_is_rejected(client, admin_token):
    """AC-04: a term with no definition would inject a bare word, which is not
    what the block is for."""
    resp = _create(client, admin_token, standard_term=TERM_B, definition='  ')
    assert resp.status_code >= 400
    assert resp.json().get('status_code') == 11502, resp.text[:200]


def test_an_over_long_definition_is_rejected(client, admin_token):
    """AC-04: the boundary is 200 characters — 201 must fail, not truncate."""
    resp = _create(client, admin_token, standard_term=TERM_B, definition='长' * 201)
    assert resp.status_code >= 400
    assert resp.json().get('status_code') == 11502, resp.text[:200]


def test_a_duplicate_standard_term_is_rejected(client, admin_token, created_term_ids):
    """AC-02: uniqueness is per tenant and spans aliases too."""
    first = assert_resp_200(_create(client, admin_token, standard_term=TERM_A, definition='释义甲'))
    created_term_ids.append(first['id'])

    duplicate = _create(client, admin_token, standard_term=TERM_A, definition='另一份释义')
    assert duplicate.status_code >= 400
    assert duplicate.json().get('status_code') == 11503, duplicate.text[:200]


def test_an_alias_colliding_with_an_existing_standard_term_is_rejected(client, admin_token, created_term_ids):
    """AC-03: the collision is checked in BOTH directions — a new alias that
    matches an existing standard term is as ambiguous as the reverse."""
    first = assert_resp_200(_create(client, admin_token, standard_term=TERM_A, definition='释义甲'))
    created_term_ids.append(first['id'])

    conflicting = _create(client, admin_token, standard_term=TERM_B, definition='释义乙', aliases=[TERM_A])
    assert conflicting.status_code >= 400
    assert conflicting.json().get('status_code') == 11503, conflicting.text[:200]


def test_deleting_a_missing_term_returns_11501(client, admin_token):
    """11501 rather than 404: "not found" and "not yours" share a code on
    purpose, so a caller cannot probe another tenant's ids (INV-16)."""
    resp = client.delete(f'{TERMS_URL}/999999999', headers=auth_headers(admin_token))
    assert resp.status_code >= 400
    assert resp.json().get('status_code') == 11501, resp.text[:200]


# ---------------------------------------------------------------------------
# Import: preview, both modes, and the failure path (AC-07 ~ AC-10)
# ---------------------------------------------------------------------------


def _import(client, token, content: bytes, filename: str, mode: str, *, preview: bool):
    url = f'{TERMS_URL}/import/preview' if preview else f'{TERMS_URL}/import'
    return client.post(
        url,
        files={'file': (filename, content, 'application/octet-stream')},
        data={'mode': mode},
        headers=auth_headers(token),
    )


def _csv(rows: list[list[str]]) -> bytes:
    return '\n'.join(','.join(row) for row in rows).encode('utf-8')


def test_import_preview_reports_the_counts_without_writing(client, admin_token):
    """AC-07 / AC-09: the preview is what makes an overwrite safe to confirm."""
    content = _csv([['标准词', '别名', '释义'], [TERM_A, ALIAS_A, '释义甲'], [TERM_B, '', '释义乙']])

    preview = assert_resp_200(_import(client, admin_token, content, 'terms.csv', 'append', preview=True))

    assert preview['mode'] == 'append'
    assert preview['added'] == 2
    # Nothing was written by the preview call.
    listed = assert_resp_200(
        client.get(TERMS_URL, params={'page': 1, 'page_size': 100}, headers=auth_headers(admin_token))
    )
    assert all(row['standard_term'] != TERM_A for row in listed['data'])


def test_a_txt_file_is_refused_with_11505(client, admin_token):
    """Design decision 8: three columns cannot be carried by a plain word list,
    so txt is refused at the door rather than imported as zero usable rows."""
    content = _csv([[TERM_A, ALIAS_A, '释义甲']]).decode().encode('utf-8')

    resp = _import(client, admin_token, content, 'terms.txt', 'append', preview=True)

    assert resp.status_code >= 400
    assert resp.json().get('status_code') == 11505, resp.text[:200]


def test_an_unparseable_file_is_refused_and_writes_nothing(client, admin_token):
    """AC-09: zero writes, not partial writes."""
    resp = _import(client, admin_token, b'', 'terms.csv', 'append', preview=False)

    assert resp.status_code >= 400
    assert resp.json().get('status_code') == 11505, resp.text[:200]

    listed = assert_resp_200(
        client.get(TERMS_URL, params={'page': 1, 'page_size': 100}, headers=auth_headers(admin_token))
    )
    assert all(row['standard_term'] != TERM_A for row in listed['data'])


def test_append_import_adds_without_removing(client, admin_token, created_term_ids):
    """AC-08: the non-destructive mode really is non-destructive."""
    first = assert_resp_200(_create(client, admin_token, standard_term=TERM_A, definition='原有释义'))
    created_term_ids.append(first['id'])

    content = _csv([[TERM_B, '', '释义乙']])
    result = assert_resp_200(_import(client, admin_token, content, 'terms.csv', 'append', preview=False))
    assert result['added'] == 1

    listed = assert_resp_200(
        client.get(TERMS_URL, params={'page': 1, 'page_size': 100}, headers=auth_headers(admin_token))
    )
    names = {row['standard_term'] for row in listed['data']}
    assert TERM_A in names, 'append removed an existing term'
    assert TERM_B in names
    created_term_ids.extend(row['id'] for row in listed['data'] if row['standard_term'] == TERM_B)


def test_the_import_obeys_the_same_rules_as_manual_entry(client, admin_token):
    """AC-10: a row with no definition fails the file, exactly as manual entry
    refuses it — the import must not be a way around the validation."""
    content = _csv([[TERM_A, ALIAS_A]])  # 只有两列

    resp = _import(client, admin_token, content, 'terms.csv', 'append', preview=True)

    assert resp.status_code >= 400
    assert resp.json().get('status_code') == 11505, resp.text[:200]
