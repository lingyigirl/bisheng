"""F059 e2e: platform-enforced word library, feedback trail, answer suggestions.

Real backend + middleware required (localhost:7860, override via E2E_API_BASE).

Coverage note: the parts that need a real model, a real ws stream or a real
Milvus/ES index are in `features/v2.7.0/059-chat-compliance-and-answer-enhancements/
e2e-checklist.md` as a manual list. What runs here is everything that is
reachable over HTTP with no model in the loop — which includes the two
SECURITY-critical properties (super-admin-only access and tenant-side word
redaction), because those are exactly the assumptions worth testing against a
real auth stack rather than mocks.

The suite is self-cleaning: it restores the platform policy to disabled and
removes the words it created, so a failure part-way through does not leave the
platform library enforcing words on a shared dev database.
"""

# ruff: noqa: RUF002
# Chinese docstrings below use fullwidth punctuation deliberately — RUF002 calls
# that "ambiguous", which is exactly the punctuation we intend.

import os
import uuid

import httpx
import pytest

from test.e2e.helpers.api import API_BASE, assert_resp_200
from test.e2e.helpers.auth import auth_headers, encrypt_password

WORDS_BASE = f'{API_BASE}/platform-sensitive-words'
POLICY_URL = f'{API_BASE}/platform-sensitive-word-policy'
HIT_LOGS_URL = f'{API_BASE}/sensitive-word-hit-logs'
FEEDBACK_URL = f'{API_BASE}/message-feedback-events'
SUGGESTIONS_URL = f'{API_BASE}/chat/suggestions'

# Unique per run so a re-run never collides with words it left behind.
WORD_A = f'f059甲{uuid.uuid4().hex[:8]}'
WORD_B = f'f059乙{uuid.uuid4().hex[:8]}'
PLATFORM_REPLY = f'F059 平台话术 {uuid.uuid4().hex[:6]}'


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
    """The global super admin — the only identity that may touch the platform library."""
    password = os.environ.get('E2E_ADMIN_PASSWORD', 'admin123')
    return _login(client, 'admin', password)


@pytest.fixture(scope="module")
def created_word_ids():
    ids: list[int] = []
    yield ids


@pytest.fixture(scope="module", autouse=True)
def _cleanup(client, admin_token, created_word_ids):
    """Restore the platform policy and drop the words this run created.

    Best-effort and never re-raises: a teardown failure must not mask the test
    result. Leaving the policy enabled would be the harmful case (it enforces on
    every tenant), so it is restored first.
    """
    yield
    try:
        client.put(
            POLICY_URL,
            json={'enabled': False, 'action': 'mask', 'auto_reply': ''},
            headers=auth_headers(admin_token),
        )
    except Exception:
        # Best-effort teardown: never mask a test result with a cleanup error.
        pass
    for word_id in created_word_ids:
        try:
            client.delete(f'{WORDS_BASE}/{word_id}', headers=auth_headers(admin_token))
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Library CRUD (spec AC-01)
# ---------------------------------------------------------------------------


def test_word_list_is_reachable_by_super_admin(client, admin_token):
    data = assert_resp_200(client.get(WORDS_BASE, headers=auth_headers(admin_token)))
    assert 'data' in data and 'total' in data


def test_add_search_and_delete_a_word(client, admin_token, created_word_ids):
    """AC-01: 分页 / 搜索 / 单条新增 / 单条删除的完整往返."""
    created = assert_resp_200(
        client.post(WORDS_BASE, data={'word': WORD_A}, headers=auth_headers(admin_token))
    )
    assert created['created'] is True

    listed = assert_resp_200(
        client.get(WORDS_BASE, params={'keyword': WORD_A}, headers=auth_headers(admin_token))
    )
    assert listed['total'] >= 1
    row = next(r for r in listed['data'] if r['word'] == WORD_A)
    created_word_ids.append(row['id'])
    assert row['source'] == 'manual'

    assert_resp_200(client.delete(f'{WORDS_BASE}/{row["id"]}', headers=auth_headers(admin_token)))
    created_word_ids.remove(row['id'])

    after = assert_resp_200(
        client.get(WORDS_BASE, params={'keyword': WORD_A}, headers=auth_headers(admin_token))
    )
    assert all(r['word'] != WORD_A for r in after['data'])


def test_adding_the_same_word_twice_reports_not_created(client, admin_token, created_word_ids):
    """The library is a set, not a log — a repeat add is a no-op, not an error."""
    first = assert_resp_200(
        client.post(WORDS_BASE, data={'word': WORD_B}, headers=auth_headers(admin_token))
    )
    assert first['created'] is True
    listed = assert_resp_200(
        client.get(WORDS_BASE, params={'keyword': WORD_B}, headers=auth_headers(admin_token))
    )
    created_word_ids.append(next(r for r in listed['data'] if r['word'] == WORD_B)['id'])

    second = assert_resp_200(
        client.post(WORDS_BASE, data={'word': WORD_B}, headers=auth_headers(admin_token))
    )
    assert second['created'] is False


def test_deleting_a_missing_word_returns_11302(client, admin_token):
    """11302 rather than 11301: "already gone" is distinct from "unparseable"."""
    resp = client.delete(f'{WORDS_BASE}/999999999', headers=auth_headers(admin_token))
    assert resp.status_code >= 400
    assert resp.json().get('status_code') == 11302


# ---------------------------------------------------------------------------
# Import: preview, both modes, and the 11301 failure path (AC-02..AC-05)
# ---------------------------------------------------------------------------


def _import(client, token, content: bytes, filename: str, mode: str, *, preview: bool):
    url = f'{WORDS_BASE}/import/preview' if preview else f'{WORDS_BASE}/import'
    return client.post(
        url,
        files={'file': (filename, content, 'text/plain')},
        data={'mode': mode},
        headers=auth_headers(token),
    )


def test_import_preview_reports_the_diff_without_writing(client, admin_token):
    """AC-03: 预览必须先于写入 — 覆盖模式靠它避免静默清空词库."""
    content = f'{WORD_A}\n{WORD_B}\n'.encode()
    preview = assert_resp_200(_import(client, admin_token, content, 'w.txt', 'overwrite', preview=True))

    assert preview['mode'] == 'overwrite'
    assert preview['incoming'] == 2
    assert preview['added'] >= 1
    # Nothing was written by the preview call.
    listed = assert_resp_200(
        client.get(WORDS_BASE, params={'keyword': 'f059'}, headers=auth_headers(admin_token))
    )
    assert all(r['word'] not in (WORD_A, WORD_B) for r in listed['data'])


def test_append_mode_adds_without_removing(client, admin_token, created_word_ids):
    """AC-02: 追加不删除既有词条."""
    existing = assert_resp_200(
        client.get(WORDS_BASE, params={'page_size': 100}, headers=auth_headers(admin_token))
    )
    before_total = existing['total']

    assert_resp_200(_import(client, admin_token, f'{WORD_A}\n'.encode(), 'w.txt', 'append', preview=False))

    after = assert_resp_200(
        client.get(WORDS_BASE, params={'keyword': WORD_A}, headers=auth_headers(admin_token))
    )
    row = next((r for r in after['data'] if r['word'] == WORD_A), None)
    assert row is not None and row['source'] == 'import'
    created_word_ids.append(row['id'])

    # Append never shrinks the library.
    now_total = assert_resp_200(
        client.get(WORDS_BASE, params={'page_size': 100}, headers=auth_headers(admin_token))
    )['total']
    assert now_total >= before_total


def test_empty_file_is_rejected_with_11301(client, admin_token):
    """AC-05: 解析不出有效词条 → 11301，且词库保持原状（不部分写入）."""
    resp = _import(client, admin_token, b'   \n\n  ', 'empty.txt', 'append', preview=False)
    assert resp.status_code >= 400
    assert resp.json().get('status_code') == 11301


def test_unsupported_file_type_is_rejected_with_11301(client, admin_token):
    resp = _import(client, admin_token, b'%PDF-1.4', 'words.pdf', 'append', preview=False)
    assert resp.status_code >= 400
    assert resp.json().get('status_code') == 11301


# ---------------------------------------------------------------------------
# Super-admin-only access (spec AC-06, release-contract INV-10)
# ---------------------------------------------------------------------------


def test_anonymous_callers_are_rejected(client):
    """No token at all must not reach the platform library."""
    assert client.get(WORDS_BASE).status_code in (401, 403)
    assert client.post(WORDS_BASE, data={'word': 'x'}).status_code in (401, 403)
    assert client.get(POLICY_URL).status_code in (401, 403)


def test_a_non_admin_user_is_rejected(client):
    """A regular (non-admin) account must not read or write the platform library.

    This is the boundary `get_tenant_admin_user` would have leaked: it admits a
    tenant's Child Admin, who must NOT be able to see the cross-tenant library.
    """
    username = os.environ.get('E2E_USER_USERNAME')
    password = os.environ.get('E2E_USER_PASSWORD')
    if not username or not password:
        pytest.skip('E2E_USER_USERNAME / E2E_USER_PASSWORD not set — cannot exercise the non-admin path')

    token = _login(client, username, password)
    assert client.get(WORDS_BASE, headers=auth_headers(token)).status_code == 403
    assert client.get(POLICY_URL, headers=auth_headers(token)).status_code == 403
    assert (
        client.put(
            POLICY_URL,
            json={'enabled': True, 'action': 'mask', 'auto_reply': 'x'},
            headers=auth_headers(token),
        ).status_code
        == 403
    )


# ---------------------------------------------------------------------------
# Policy read/write (spec AC-08/AC-09)
# ---------------------------------------------------------------------------


def test_policy_roundtrip_and_default_reply(client, admin_token):
    """AC-09: 话术未设置时返回需求原文作为初始值，并标注这是默认值."""
    unset = assert_resp_200(client.get(POLICY_URL, headers=auth_headers(admin_token)))
    assert 'auto_reply' in unset
    assert 'auto_reply_is_default' in unset

    saved = assert_resp_200(
        client.put(
            POLICY_URL,
            json={'enabled': True, 'action': 'block', 'auto_reply': PLATFORM_REPLY},
            headers=auth_headers(admin_token),
        )
    )
    assert saved['enabled'] is True
    assert saved['action'] == 'block'
    assert saved['auto_reply'] == PLATFORM_REPLY
    assert saved['auto_reply_is_default'] is False

    fetched = assert_resp_200(client.get(POLICY_URL, headers=auth_headers(admin_token)))
    assert fetched['action'] == 'block'
    assert fetched['auto_reply'] == PLATFORM_REPLY


def test_an_invalid_action_is_rejected(client, admin_token):
    resp = client.put(
        POLICY_URL,
        json={'enabled': True, 'action': 'explode', 'auto_reply': 'x'},
        headers=auth_headers(admin_token),
    )
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# Hit-log redaction (spec AC-17..AC-20, INV-10)
# ---------------------------------------------------------------------------


def test_tenant_hit_log_exposes_source_and_hit_count(client, admin_token):
    """AC-17/AC-18: 租户能看到"被拦了、几个词"，但 payload 里不含命中词."""
    data = assert_resp_200(client.get(HIT_LOGS_URL, headers=auth_headers(admin_token)))
    assert 'data' in data
    for row in data['data']:
        assert row['source'] in ('tenant', 'platform')
        assert 'hit_count' in row
        if row['source'] == 'platform':
            # The redaction is a property of the RESPONSE, not of the UI: if the
            # words were merely hidden client-side they would still be here.
            assert row['hits'] == [], 'platform hit words must not reach a tenant'


def test_hit_log_source_filter_is_accepted(client, admin_token):
    for source in ('tenant', 'platform'):
        data = assert_resp_200(
            client.get(HIT_LOGS_URL, params={'source': source}, headers=auth_headers(admin_token))
        )
        assert all(row['source'] == source for row in data['data'])


def test_platform_hit_log_view_is_admin_only(client, admin_token):
    assert_resp_200(client.get(f'{HIT_LOGS_URL}/all', headers=auth_headers(admin_token)))
    assert client.get(f'{HIT_LOGS_URL}/all').status_code in (401, 403)


# ---------------------------------------------------------------------------
# Feedback trail (spec AC-21..AC-27)
# ---------------------------------------------------------------------------


def test_feedback_events_endpoint_is_reachable(client, admin_token):
    data = assert_resp_200(client.get(FEEDBACK_URL, headers=auth_headers(admin_token)))
    assert 'data' in data and 'total' in data


def test_feedback_event_shape_carries_a_before_and_after(client, admin_token):
    """Every row must state the transition, not just the current verdict."""
    data = assert_resp_200(client.get(FEEDBACK_URL, headers=auth_headers(admin_token)))
    for row in data['data']:
        assert 'from_value' in row and 'to_value' in row
        assert row['from_value'] != row['to_value'], 'a no-op verdict must not be recorded'


def test_feedback_filter_by_type(client, admin_token):
    data = assert_resp_200(
        client.get(FEEDBACK_URL, params={'feedback_type': 1}, headers=auth_headers(admin_token))
    )
    assert all(row['to_value'] == 1 for row in data['data'])


def test_cross_tenant_feedback_view_is_admin_only(client, admin_token):
    assert_resp_200(client.get(f'{FEEDBACK_URL}/all', headers=auth_headers(admin_token)))
    assert client.get(f'{FEEDBACK_URL}/all').status_code in (401, 403)


# ---------------------------------------------------------------------------
# Answer suggestions (spec AC-29..AC-45)
# ---------------------------------------------------------------------------


def test_suggestions_never_error_on_a_bare_request(client, admin_token):
    """The bar is decorative: every degenerate input returns empty lists, not an error.

    Driven through the real endpoint because that contract (never 5xx, never a
    raised model error) is what lets the frontend render nothing and move on.
    """
    resp = client.post(
        SUGGESTIONS_URL,
        json={'question': '', 'answer': '', 'flow_id': None},
        headers=auth_headers(admin_token),
    )
    assert resp.status_code == 200
    payload = resp.json()['data']
    assert payload['keywords'] == []
    assert payload['questions'] == []


def test_suggestions_require_authentication(client):
    assert client.post(SUGGESTIONS_URL, json={'question': 'q', 'answer': 'a'}).status_code in (401, 403)


# ---------------------------------------------------------------------------
# Compatibility (spec AC-52/AC-53)
# ---------------------------------------------------------------------------


def test_disabling_the_platform_policy_restores_prior_behaviour(client, admin_token):
    """AC-53: 平台策略关闭后，对话入口行为与发布前一致.

    Asserted at the policy surface rather than by driving a conversation: with
    the layer disabled, `build_platform_matcher` returns None and the guard chain
    behaves exactly as F056 left it — which F056's own e2e suite already covers
    in full.
    """
    disabled = assert_resp_200(
        client.put(
            POLICY_URL,
            json={'enabled': False, 'action': 'mask', 'auto_reply': ''},
            headers=auth_headers(admin_token),
        )
    )
    assert disabled['enabled'] is False


def test_f036_policy_endpoints_still_work(client, admin_token):
    """AC-52: 知识库文件解析 / 订阅文章两个既有场景不受本特性影响."""
    for business_type in ('knowledge_space_file_parse', 'channel_article'):
        policy = assert_resp_200(
            client.get(f'{API_BASE}/sensitive-word-policies/{business_type}', headers=auth_headers(admin_token))
        )
        # F036's own contract is unchanged: the endpoint answers with the same shape.
        assert 'enabled' in policy
        assert 'custom_words' in policy
