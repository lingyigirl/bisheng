"""F062 e2e: archiving an 机构 must TIGHTEN visibility, never widen it (AC-20a/20b).

WHY THIS IS ITS OWN FILE
------------------------
The archive test is DESTRUCTIVE — it tears down the subtree it asserts about.
Sharing a module-scoped fixture with the cross-entry suite would make those tests
order-dependent: run this one first and the other suite's fixtures are gone,
run it last and it asserts against state a previous test already mutated. E2E
suites in this repo share a real database, so an ordering coupling here would
show up as flaky failures somewhere else entirely.

WHY THE ASSERTION IS WRITTEN AS A SUPERSET CHECK
------------------------------------------------
The failure that matters is "archiving LEAKED" — a node that was a barrier stops
being one and someone outside suddenly sees into the subtree. A test that only
checked "the subtree still sees its own libraries" would pass even if archiving
had widened access for everyone. So the outsider's visible set is captured
BEFORE and AFTER, and the assertion is on the DIFFERENCE: nothing may be newly
visible. Both directions are asserted (the subtree loses access; the outsider
gains none), because AC-20a and AC-20b are two halves of the same claim.
"""

import os
import uuid

import httpx
import pytest

from test.e2e.helpers.api import API_BASE

PREFIX = 'e2e-f062a-'
PASSWORD = 'Test_pass_123'
TYPE_NORMAL = 0
_PAGE_SIZE = 100

_STATE: dict = {}


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
    body = client.post(
        f'{API_BASE}/user/login',
        json={'user_name': username, 'password': _encrypt_password(client, password)},
    ).json()
    assert body['status_code'] == 200, f'Login failed for {username}: {body.get("status_message")}'
    return body['data']['access_token']


def _auth(token: str) -> dict:
    return {'Cookie': f'access_token_cookie={token}'}


def _ok(resp: httpx.Response):
    assert resp.status_code == 200, f'HTTP {resp.status_code}: {resp.text[:300]}'
    body = resp.json()
    assert body['status_code'] == 200, f'Business error {body["status_code"]}: {body.get("status_message")}'
    return body.get('data')


def _create_user(client, token, name) -> int:
    return int(
        _ok(
            client.post(
                f'{API_BASE}/user/create',
                json={'user_name': name, 'password': _encrypt_password(client, PASSWORD)},
                headers=_auth(token),
            )
        )['user_id']
    )


def _create_dept(client, token, name, parent_id, is_department=None) -> dict:
    payload = {'name': name, 'parent_id': parent_id}
    if is_department is not None:
        payload['is_department'] = is_department
    return _ok(client.post(f'{API_BASE}/departments/', json=payload, headers=_auth(token)))


def _add_member(client, token, dept_id: str, user_id: int) -> None:
    _ok(
        client.post(
            f'{API_BASE}/departments/{dept_id}/members',
            json={'user_ids': [user_id], 'is_primary': 0},
            headers=_auth(token),
        )
    )


def _embedding_model_id(client, token) -> int:
    payload = _ok(client.get(f'{API_BASE}/llm/knowledge', headers=_auth(token)))
    cfg = payload.get('data', payload) if isinstance(payload, dict) else {}
    model_id = cfg.get('embedding_model_id') if isinstance(cfg, dict) else None
    assert model_id, f'no embedding model configured: {str(payload)[:200]}'
    return int(model_id)


def _create_knowledge(client, token, name, model_id) -> int:
    return int(
        _ok(
            client.post(
                f'{API_BASE}/knowledge/create',
                json={'name': name, 'type': TYPE_NORMAL, 'description': 'F062 archive e2e', 'model': str(model_id)},
                headers=_auth(token),
            )
        )['id']
    )


def _delete_knowledge(client, token, knowledge_id: int) -> None:
    import json as _json

    client.request('DELETE', f'{API_BASE}/knowledge/', content=_json.dumps({'knowledge_id': knowledge_id}), headers=_auth(token))


def _assign_department(client, token, knowledge_id: int, department_id):
    return client.put(
        f'{API_BASE}/knowledge/department',
        json={'knowledge_id': knowledge_id, 'department_id': department_id},
        headers=_auth(token),
    )


def _visible(client, token) -> set[int]:
    data = _ok(client.get(f'{API_BASE}/knowledge', params={'page_size': _PAGE_SIZE, 'type': TYPE_NORMAL}, headers=_auth(token)))
    rows = data.get('data', []) if isinstance(data, dict) else data
    return {int(r['id']) for r in rows}


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
    """Build a barrier-inside-a-barrier chart, then archive the outer one.

        root
        ├── outside (部门)        owns lib_outside   — user_outside is a member
        └── barrier (机构)        owns lib_barrier
            └── inner (部门)      owns lib_inner     — user_inner is a member

    Before: `outside` is a PEER of `barrier` under the root, so it shares the
    root's org domain and CAN see the root's own libraries — but `barrier` is an
    org, so everything under it is closed to `outside`.
    """
    headers = _auth(admin_token)

    # cleanup leftovers (knowledge first, then departments deepest-first)
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
    nodes: list = []

    def _flatten(items):
        for node in items or []:
            if str(node.get('name', '')).startswith(PREFIX):
                nodes.append(node)
            _flatten(node.get('children'))

    _flatten(found.get('roots', []) if isinstance(found, dict) else (found or []))
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

    suffix = uuid.uuid4().hex[:6]
    model_id = _embedding_model_id(client, admin_token)

    outside = _create_dept(client, admin_token, f'{PREFIX}outside-{suffix}', root_id, is_department=True)
    barrier = _create_dept(client, admin_token, f'{PREFIX}barrier-{suffix}', root_id, is_department=False)
    inner = _create_dept(client, admin_token, f'{PREFIX}inner-{suffix}', int(barrier['id']), is_department=True)

    lib_outside = _create_knowledge(client, admin_token, f'{PREFIX}lib-outside-{suffix}', model_id)
    lib_barrier = _create_knowledge(client, admin_token, f'{PREFIX}lib-barrier-{suffix}', model_id)
    lib_inner = _create_knowledge(client, admin_token, f'{PREFIX}lib-inner-{suffix}', model_id)

    for lib, dept in ((lib_outside, outside), (lib_barrier, barrier), (lib_inner, inner)):
        _ok(_assign_department(client, admin_token, lib, int(dept['id'])))

    user_outside = _create_user(client, admin_token, f'{PREFIX}user-out-{suffix}')
    user_inner = _create_user(client, admin_token, f'{PREFIX}user-in-{suffix}')
    _add_member(client, admin_token, outside['dept_id'], user_outside)
    _add_member(client, admin_token, inner['dept_id'], user_inner)

    _STATE.update(
        {
            'outside': outside,
            'barrier': barrier,
            'inner': inner,
            'lib_outside': lib_outside,
            'lib_barrier': lib_barrier,
            'lib_inner': lib_inner,
            'user_outside': user_outside,
            'user_inner': user_inner,
            'inner_token': _login(client, f'{PREFIX}user-in-{suffix}', PASSWORD),
            'outside_token': _login(client, f'{PREFIX}user-out-{suffix}', PASSWORD),
        }
    )

    yield _STATE

    for lib in (lib_outside, lib_barrier, lib_inner):
        try:
            _delete_knowledge(client, admin_token, lib)
        except Exception:
            pass


# ---------------------------------------------------------------------------
# AC-19 — the barrier holds before anything is archived (the control)
# ---------------------------------------------------------------------------


def test_before_archive_the_barrier_holds(client):
    """Control case: without it, the after-archive assertion proves nothing.

    If `outside` could already see into `barrier`, then "no change after
    archiving" would be vacuously true.
    """
    s = _STATE
    ids = _visible(client, s['outside_token'])
    assert s['lib_outside'] in ids, 'sanity: 部门 sees its own library'
    assert s['lib_inner'] not in ids, 'CONTROL: the 机构 barrier is closed beforehand'


# ---------------------------------------------------------------------------
# AC-20a / AC-20b — archiving tightens
# ---------------------------------------------------------------------------


def test_archiving_the_org_does_not_widen_an_outsiders_view(client, admin_token):
    """AC-20a written in the direction that can fail: no NEW visibility.

    An archived 机构 must disappear from the tree, taking its whole subtree with
    it. The dangerous outcome is the opposite — the barrier dissolving on archive
    and the subtree's libraries becoming visible to peers that could not see them
    before. Asserted as a superset check so a leak is caught, and separately that
    the department's own library is no longer granted (AC-20b's tighten half).
    """
    s = _STATE
    before = _visible(client, s['outside_token'])

    # Archive bottom-up: a department cannot be archived while it still has
    # active children or members.
    client.delete(f'{API_BASE}/departments/{s["inner"]["dept_id"]}/members/{s["user_inner"]}', headers=_auth(admin_token))
    client.delete(f'{API_BASE}/departments/{s["inner"]["dept_id"]}', headers=_auth(admin_token))
    client.delete(f'{API_BASE}/departments/{s["inner"]["dept_id"]}/purge', headers=_auth(admin_token))

    _ok(client.delete(f'{API_BASE}/departments/{s["barrier"]["dept_id"]}', headers=_auth(admin_token)))

    after = _visible(client, s['outside_token'])

    leaked = after - before
    assert not leaked, f'archiving a 机构 made libraries newly visible to a peer: {sorted(leaked)}'
    assert s['lib_inner'] not in after, 'the archived subtree must not keep granting'
    assert s['lib_barrier'] not in after, 'the archived department itself must not keep granting'


def test_the_subtree_member_loses_the_archived_org_domain(client):
    """AC-20b: members inside the archived subtree lose what the org granted them.

    The symmetric half — tightening must actually reach the people it was
    granting to, not just stop granting to outsiders.
    """
    s = _STATE
    ids = _visible(client, s['inner_token'])
    assert s['lib_inner'] not in ids, 'the inner library is gone with its department'
    assert s['lib_barrier'] not in ids, 'and so is the archived org own library'


def test_archived_department_is_no_longer_writable(client, admin_token):
    """AC-20a companion: an archived node must reject further edits.

    Restored during code review after the readonly guard was briefly lost while
    wiring the marker gate — an archived ("deleted") department that still
    accepts marker changes keeps participating in visibility decisions. This is
    the end-to-end counterpart of
    `test_f062_node_type_permission.py::test_archived_department_is_still_readonly`.
    """
    resp = client.put(
        f'{API_BASE}/departments/{_STATE["barrier"]["dept_id"]}',
        json={'is_department': True},
        headers=_auth(admin_token),
    )
    assert resp.status_code == 200, resp.text[:200]
    assert resp.json()['status_code'] != 200, 'an archived department accepted a marker change'
