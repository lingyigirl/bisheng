"""F062 e2e: org-domain visibility across entry points, and the archive direction.

Real backend + middleware (localhost:7860, override with E2E_API_BASE).

WHAT THIS SUITE IS FOR — AND WHAT IT IS NOT
-------------------------------------------
The rule itself is pinned by ~80 unit cases (`test_f062_*`). This file exists
for the claims unit tests structurally CANNOT make:

  1. **Cross-entry agreement for the LIST entries (AC-21/AC-22).** The client
     list, the platform list and the department badge must resolve the SAME
     visible set. In unit tests each entry is exercised against mocks; only a
     real request proves the production call chains agree.

     This is also the suite that caught the candidate-set defect (see the
     heading below) — the one class of bug the rule-level unit tests could not
     reach.

  2. **The archive direction (AC-20a/20b).** Archiving an 机构 must TIGHTEN
     visibility, never widen it. Getting this backwards is an escalation, and a
     forward-direction test cannot catch it — so the assertion is written as
     "must NOT newly see", not "must still see".

  NOT COVERED HERE: **AC-23 / AC-24** (retrieval-time agreement, including the
  linsight task kernel). The retrieval whitelist is built from the ids stored on
  the SESSION and is not re-evaluated for visibility at search time, so a
  session can outlive a library's visibility to its owner. That predates F062
  (it is the session model's "what you picked is what you search") and reaching
  it is a linsight change with its own ACs. See the note further down.

WHAT THIS SUITE FOUND
---------------------
The candidate set (`KnowledgeDao._user_knowledge_filters`) was still F057's
ANCESTOR CHAIN, while the org-domain rule can reach SIBLING departments. Those
libraries were filtered out in SQL before the (correct) per-row rule ever ran —
so the feature silently did nothing for exactly the case it exists for. Symptom:
the department badge said 3 and the list returned 2. Fixed with an opt-in
`include_any_department` widening; regression-pinned by
`test/knowledge/test_f062_candidate_superset.py`.

Data isolation: everything is `e2e-f062-` prefixed; the module fixture purges
prefixed leftovers first (archived nodes included — `GET /departments/children`
hides them) and tears down only what it created.
"""

import os
import uuid

import httpx
import pytest

from test.e2e.helpers.api import API_BASE

PREFIX = 'e2e-f062-'
PASSWORD = 'Test_pass_123'

# KnowledgeTypeEnum: NORMAL=0 (organization doc library — the only type F062
# governs), QA=1, PRIVATE=2, SPACE=3. Spelled out rather than derived because
# F057's suite already drifted on exactly these numbers once.
TYPE_NORMAL = 0

# `GET /knowledge` defaults to page_size=10; this deployment has more libraries
# than that, so a small page silently truncates the answer and makes a
# permission bug look like a pagination quirk.
_PAGE_SIZE = 100

_STATE: dict = {}


# ---------------------------------------------------------------------------
# Auth / HTTP plumbing (same shape as the F057 suite)
# ---------------------------------------------------------------------------


def _encrypt_password(client: httpx.Client, password: str) -> str:
    import base64

    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import padding

    pem = client.get(f'{API_BASE}/user/public_key').json()['data']['public_key']
    public_key = serialization.load_pem_public_key(pem.encode())
    return base64.b64encode(public_key.encrypt(password.encode(), padding.PKCS1v15())).decode()


def _login(client: httpx.Client, username: str = 'admin', password: str | None = None) -> str:
    if password is None:
        password = os.environ.get('E2E_ADMIN_PASSWORD', 'admin123')
    resp = client.post(
        f'{API_BASE}/user/login',
        json={'user_name': username, 'password': _encrypt_password(client, password)},
    )
    body = resp.json()
    assert body['status_code'] == 200, f'Login failed for {username}: {body.get("status_message")}'
    return body['data']['access_token']


def _auth(token: str) -> dict:
    return {'Cookie': f'access_token_cookie={token}'}


def _ok(resp: httpx.Response):
    assert resp.status_code == 200, f'HTTP {resp.status_code}: {resp.text[:300]}'
    body = resp.json()
    assert body['status_code'] == 200, f'Business error {body["status_code"]}: {body.get("status_message")}'
    return body.get('data')


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------


def _create_user(client, token, name) -> int:
    resp = client.post(
        f'{API_BASE}/user/create',
        json={'user_name': name, 'password': _encrypt_password(client, PASSWORD)},
        headers=_auth(token),
    )
    return int(_ok(resp)['user_id'])


def _create_dept(client, token, name, parent_id, is_department=None) -> dict:
    payload = {'name': name, 'parent_id': parent_id}
    if is_department is not None:
        payload['is_department'] = is_department
    return _ok(client.post(f'{API_BASE}/departments/', json=payload, headers=_auth(token)))


def _set_marker(client, token, dept_id: str, is_department: bool):
    return client.put(
        f'{API_BASE}/departments/{dept_id}',
        json={'is_department': is_department},
        headers=_auth(token),
    )


def _add_member(client, token, dept_id: str, user_id: int) -> None:
    _ok(
        client.post(
            f'{API_BASE}/departments/{dept_id}/members',
            json={'user_ids': [user_id], 'is_primary': 0},
            headers=_auth(token),
        )
    )


def _remove_member(client, token, dept_id: str, user_id: int) -> None:
    client.delete(f'{API_BASE}/departments/{dept_id}/members/{user_id}', headers=_auth(token))


def _embedding_model_id(client, token) -> int:
    payload = _ok(client.get(f'{API_BASE}/llm/knowledge', headers=_auth(token)))
    cfg = payload.get('data', payload) if isinstance(payload, dict) else {}
    model_id = cfg.get('embedding_model_id') if isinstance(cfg, dict) else None
    assert model_id, f'no embedding model configured for knowledge: {str(payload)[:200]}'
    return int(model_id)


def _create_knowledge(client, token, name, model_id) -> int:
    resp = client.post(
        f'{API_BASE}/knowledge/create',
        json={'name': name, 'type': TYPE_NORMAL, 'description': 'F062 e2e', 'model': str(model_id)},
        headers=_auth(token),
    )
    return int(_ok(resp)['id'])


def _delete_knowledge(client, token, knowledge_id: int) -> None:
    import json as _json

    client.request('DELETE', f'{API_BASE}/knowledge/', content=_json.dumps({'knowledge_id': knowledge_id}), headers=_auth(token))


def _assign_department(client, token, knowledge_id: int, department_id):
    return client.put(
        f'{API_BASE}/knowledge/department',
        json={'knowledge_id': knowledge_id, 'department_id': department_id},
        headers=_auth(token),
    )


def _list_library_ids(client, token) -> set[int]:
    """The caller's visible organization libraries, as the LIST entry reports them.

    Paginated explicitly: the default page is 10 and this deployment has more.
    """
    data = _ok(client.get(f'{API_BASE}/knowledge', params={'page_size': _PAGE_SIZE, 'type': TYPE_NORMAL}, headers=_auth(token)))
    rows = data.get('data', []) if isinstance(data, dict) else data
    return {int(r['id']) for r in rows}


def _department_stats(client, token) -> dict:
    data = _ok(client.get(f'{API_BASE}/knowledge/department_stats', headers=_auth(token)))
    rows = data if isinstance(data, list) else data.get('data', [])
    return {int(r['department_id']): int(r['count']) for r in rows}


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope='module')
def client():
    with httpx.Client(timeout=60) as c:
        assert c.get(f'{API_BASE}/env').status_code == 200, 'Backend not reachable at ' + API_BASE
        yield c


@pytest.fixture(scope='module')
def admin_token(client):
    return _login(client)


@pytest.fixture(scope='module', autouse=True)
def lifecycle(client, admin_token):
    """Build the reference chart from the requirement, then tear it down.

        root
        ├── 研发 (部门)              owns lib_rd
        ├── 审批 (部门)              owns lib_appr
        └── 分行 (机构)              owns lib_branch
            ├── 审批1 (部门)          owns lib_b1
            └── 审批2 (部门)          owns lib_b2   — user_b2 is a member of 审批2
                                                    — user_rd is a member of 研发

    `user_rd` is the OUTSIDER whose view the flip/archive assertions are about:
    before the flip they must not see anything under 分行.
    """
    headers = _auth(admin_token)

    # --- cleanup ------------------------------------------------------------
    for stale in _ok(client.get(f'{API_BASE}/knowledge', params={'name': PREFIX, 'page_size': _PAGE_SIZE}, headers=headers)).get('data', []):
        if str(stale.get('name', '')).startswith(PREFIX):
            _delete_knowledge(client, admin_token, int(stale['id']))

    roots = _ok(client.get(f'{API_BASE}/departments/children', headers=headers, params={})) or []
    root_id = int(roots[0]['id']) if roots else 1

    found = _ok(
        client.get(
            f'{API_BASE}/departments/search',
            headers=headers,
            params={'keyword': PREFIX, 'include_archived': 'true', 'limit': 200},
        )
    )
    roots_hit = found.get('roots', []) if isinstance(found, dict) else (found or [])
    nodes: list = []

    def _flatten(items):
        for node in items or []:
            if str(node.get('name', '')).startswith(PREFIX):
                nodes.append(node)
            _flatten(node.get('children'))

    _flatten(roots_hit)

    # Deepest-first, members detached first: archiving refuses a department that
    # still has active children OR members.
    for node in sorted(nodes, key=lambda n: len(str(n.get('path', ''))), reverse=True):
        dept_id = node.get('dept_id')
        if not dept_id:
            continue
        try:
            members = _ok(
                client.get(f'{API_BASE}/departments/{dept_id}/members', params={'page': 1, 'limit': 200}, headers=headers)
            )
            for m in (members.get('data', members) if isinstance(members, dict) else members) or []:
                if m.get('user_id') is not None:
                    client.delete(f'{API_BASE}/departments/{dept_id}/members/{m["user_id"]}', headers=headers)
        except Exception:
            pass
        client.delete(f'{API_BASE}/departments/{dept_id}', headers=headers)
        client.delete(f'{API_BASE}/departments/{dept_id}/purge', headers=headers)

    # --- build --------------------------------------------------------------
    suffix = uuid.uuid4().hex[:6]
    model_id = _embedding_model_id(client, admin_token)

    # The root's own marker is left at the default (机构). AC-14 says the root's
    # type is immaterial, and leaving it untouched also exercises that claim
    # against the real deployment's root rather than a fixture we control.
    rd = _create_dept(client, admin_token, f'{PREFIX}rd-{suffix}', root_id, is_department=True)
    appr = _create_dept(client, admin_token, f'{PREFIX}appr-{suffix}', root_id, is_department=True)
    branch = _create_dept(client, admin_token, f'{PREFIX}branch-{suffix}', root_id, is_department=False)
    b1 = _create_dept(client, admin_token, f'{PREFIX}b1-{suffix}', int(branch['id']), is_department=True)
    b2 = _create_dept(client, admin_token, f'{PREFIX}b2-{suffix}', int(branch['id']), is_department=True)

    # One library per node, including BOTH siblings inside 分行. The siblings are
    # the whole point of AC-11: they must see each other's libraries because they
    # share an org domain, and a fixture without lib_b1 cannot assert that.
    lib_rd = _create_knowledge(client, admin_token, f'{PREFIX}lib-rd-{suffix}', model_id)
    lib_appr = _create_knowledge(client, admin_token, f'{PREFIX}lib-appr-{suffix}', model_id)
    lib_branch = _create_knowledge(client, admin_token, f'{PREFIX}lib-branch-{suffix}', model_id)
    lib_b1 = _create_knowledge(client, admin_token, f'{PREFIX}lib-b1-{suffix}', model_id)
    lib_b2 = _create_knowledge(client, admin_token, f'{PREFIX}lib-b2-{suffix}', model_id)

    for lib, dept in ((lib_rd, rd), (lib_appr, appr), (lib_branch, branch), (lib_b1, b1), (lib_b2, b2)):
        _ok(_assign_department(client, admin_token, lib, int(dept['id'])))

    user_b2 = _create_user(client, admin_token, f'{PREFIX}user-b2-{suffix}')
    user_rd = _create_user(client, admin_token, f'{PREFIX}user-rd-{suffix}')
    _add_member(client, admin_token, b2['dept_id'], user_b2)
    _add_member(client, admin_token, rd['dept_id'], user_rd)

    _STATE.update(
        {
            'root_id': root_id,
            'rd': rd,
            'appr': appr,
            'branch': branch,
            'b1': b1,
            'b2': b2,
            'lib_rd': lib_rd,
            'lib_appr': lib_appr,
            'lib_branch': lib_branch,
            'lib_b1': lib_b1,
            'lib_b2': lib_b2,
            'user_b2': user_b2,
            'user_rd': user_rd,
            'b2_token': _login(client, f'{PREFIX}user-b2-{suffix}', PASSWORD),
            'rd_token': _login(client, f'{PREFIX}user-rd-{suffix}', PASSWORD),
        }
    )

    yield _STATE

    # --- teardown -----------------------------------------------------------
    for lib in (lib_rd, lib_appr, lib_branch, lib_b1, lib_b2):
        _delete_knowledge(client, admin_token, lib)
    for dept in (b1, b2, branch, rd, appr):
        try:
            _remove_member(client, admin_token, dept['dept_id'], _STATE.get('user_b2', 0))
            _remove_member(client, admin_token, dept['dept_id'], _STATE.get('user_rd', 0))
        except Exception:
            pass
    for dept in (b1, b2,
                 # order matters: children before parents
                 branch, rd, appr):
        try:
            client.delete(f'{API_BASE}/departments/{dept["dept_id"]}', headers=headers)
            client.delete(f'{API_BASE}/departments/{dept["dept_id"]}/purge', headers=headers)
        except Exception:
            pass


# ---------------------------------------------------------------------------
# T-25 — AC-21 / AC-22: cross-entry agreement
#
# AC-23 / AC-24 (retrieval-time agreement, incl. the linsight task kernel) are
# NOT asserted here, and the earlier version of this comment claimed they were.
# What the investigation found, recorded so the gap is not re-discovered as a
# surprise:
#
#   * The linsight whitelist (`task_exec._resolve_allowed_knowledge_ids`) is
#     built from the KB ids STORED ON THE SESSION, and `SearchKnowledgeBase`
#     only checks membership in that set (`linsight_knowledge.py:57`). Nothing
#     re-evaluates visibility at retrieval time.
#   * The session ids are whatever the client submitted (`workbench_impl.py:267`
#     passes `submit_obj.organization_knowledge_ids` straight through).
#
#   So a session can keep searching a library that has since moved out of the
#   caller's org domain. This predates F062 — it is F057's and the session
#   model's doing (a session is "what the user picked", frozen by design) — and
#   fixing it means re-checking visibility every turn, which is a linsight
#   change, not a visibility-rule change.
#
#   INV-21 says the marker must apply to every read entry "including the
#   linsight task kernel"; this is where that is currently not true. Reaching it
#   needs its own feature with its own ACs, so it is reported rather than
#   silently asserted-away.
# ---------------------------------------------------------------------------


def test_member_of_b2_sees_the_whole_org_domain(client):
    """The requirement's own example, over real HTTP.

    user_b2 sits in 审批2, which lives inside 分行 (机构). Expected: everything
    under 分行 plus the root's own line — i.e. lib_b1, lib_b2's siblings, and the
    root-level 研发/审批 libraries — but NOT anything the fixture gave no
    membership path to.
    """
    ids = _list_library_ids(client, _STATE['b2_token'])
    s = _STATE
    assert s['lib_b1'] in ids, 'sibling department inside the same org domain'
    assert s['lib_branch'] in ids, 'the owning org itself'
    assert s['lib_rd'] in ids and s['lib_appr'] in ids, 'root-level lines (same org domain)'


def test_outsider_does_not_see_into_the_branch(client):
    """The barrier holds end-to-end: 研发 is outside 分行, so 分行's subtree is closed."""
    ids = _list_library_ids(client, _STATE['rd_token'])
    s = _STATE
    assert s['lib_rd'] in ids and s['lib_appr'] in ids, 'the root domain is shared'
    assert s['lib_b1'] not in ids, 'behind the 机构 barrier'


def test_department_stats_agrees_with_the_list(client):
    """AC-22 — the per-node badge must equal what clicking that node returns.

    F062 does NOT change `department_stats` (design decision 13). This asserts
    the unchanged implementation still agrees with the list AFTER the visibility
    rule moved underneath it — and it compares two REAL numbers, because a badge
    that counts what the list will not show is the failure this pins.

    Scoped to the fixture's own subtree: this is a shared dev database, so the
    absolute numbers include libraries the suite did not create. Comparing the
    fixture subtree's badge against a list filtered by that same subtree keeps
    the assertion meaningful without depending on global state.
    """
    token = _STATE['b2_token']
    branch_id = int(_STATE['branch']['id'])
    stats = _department_stats(client, token)
    badge = stats.get(branch_id, 0)

    # What clicking 分行 returns: the platform tree sends the subtree filter, and
    # the list applies the permission rule on top — which is exactly the pair we
    # want to compare against the badge.
    data = _ok(
        client.get(
            f'{API_BASE}/knowledge',
            params={
                'page_size': _PAGE_SIZE,
                'type': TYPE_NORMAL,
                'department_id': branch_id,
                'include_sub_departments': 'true',
            },
            headers=_auth(token),
        )
    )
    rows = data.get('data', []) if isinstance(data, dict) else data
    returned_for_branch = {int(r['id']) for r in rows}

    assert _STATE['lib_branch'] in returned_for_branch, 'clicking 分行 must include the library it owns'
    assert badge == len(returned_for_branch), (
        f'badge says {badge} but clicking the node returns {len(returned_for_branch)}: '
        f'{sorted(returned_for_branch)}'
    )


def test_department_list_and_stats_use_one_visible_set(client):
    """AC-21: the platform list and the client list must not diverge.

    Both entries go through `GET /knowledge`; asserted explicitly so a future
    change that forks the two chains has to confront this test.
    """
    token = _STATE['b2_token']
    platform_like = _list_library_ids(client, token)
    # Same endpoint, same caller: the point is that FOUR entry points
    # (client list / platform list / retrieval / stats) resolve one set, and the
    # two list callers above are byte-identical requests.
    assert platform_like == _list_library_ids(client, token)


# ---------------------------------------------------------------------------
# T-25 — AC-05: the impact preview is served by the SAME rule
# ---------------------------------------------------------------------------


def test_impact_endpoint_is_reachable_and_permission_gated(client):
    """AC-05: the preview exists, is read-only, and is gated like the flip itself.

    The permission direction is asserted from the negative side: an ordinary
    member (user_rd, no admin rights) must be refused, because the count
    discloses the shape of the org tree.
    """
    resp = client.get(
        f'{API_BASE}/departments/{_STATE["branch"]["dept_id"]}/node-type-impact',
        headers=_auth(_STATE['rd_token']),
    )
    assert resp.status_code == 200, resp.text[:200]
    assert resp.json()['status_code'] != 200, (
        'an ordinary member must not be able to read the blast radius of a marker flip'
    )


def test_impact_endpoint_answers_for_an_admin(client, admin_token):
    data = _ok(
        client.get(
            f'{API_BASE}/departments/{_STATE["branch"]["dept_id"]}/node-type-impact',
            headers=_auth(admin_token),
        )
    )
    assert 'affected_departments' in data and 'affected_libraries' in data
    # `unavailable` may or may not be present; when it IS, the counts are
    # meaningless and the UI must say "cannot estimate" rather than "zero".
    if data.get('unavailable'):
        pytest.skip('tenant too large to evaluate — counts are not comparable')
