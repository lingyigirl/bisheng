"""F057 e2e: the member-scoped department chain must work for ordinary members.

This is the regression guard for a real defect: the knowledge page's department
filter called `/api/v1/departments/*`, which F038 scopes to system admins,
department admins and tenant admins — so a plain department MEMBER, exactly who
needs the filter, got 21009 on page load. `/knowledge/my_departments` is scoped
by MEMBERSHIP instead.

The suite deliberately drives BOTH endpoints with the SAME ordinary-member token:
the old one must still refuse (it was not changed, and ordinary members genuinely
have no business reading the management tree), while the new one must succeed.
That contrast is the whole point — asserting only the new endpoint would not
prove the old one is unsuitable, and asserting only the old one would not prove
the fix works.

Data isolation: departmental fixtures use the ``e2e-f057-`` prefix and are
cleaned up (see test_e2e_f057_knowledge_department.py for the shared cleanup
approach).
"""

import os
import uuid

import httpx
import pytest

from test.e2e.helpers.api import API_BASE

PREFIX = 'e2e-f057-md-'
PASSWORD = 'Test_pass_123'
STATE: dict = {}


def _encrypt_password(client: httpx.Client, password: str) -> str:
    import base64

    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import padding

    pem = client.get(f'{API_BASE}/user/public_key').json()['data']['public_key']
    public_key = serialization.load_pem_public_key(pem.encode())
    return base64.b64encode(public_key.encrypt(password.encode(), padding.PKCS1v15())).decode()


def _login(client: httpx.Client, username: str, password: str = PASSWORD) -> str:
    resp = client.post(
        f'{API_BASE}/user/login',
        json={'user_name': username, 'password': _encrypt_password(client, password)},
    )
    body = resp.json()
    assert body['status_code'] == 200, f'login {username} failed: {body.get("status_message")}'
    return body['data']['access_token']


def _auth(token: str) -> dict:
    return {'Cookie': f'access_token_cookie={token}'}


def _ok(resp: httpx.Response):
    assert resp.status_code == 200, f'HTTP {resp.status_code}: {resp.text[:300]}'
    body = resp.json()
    assert body['status_code'] == 200, f'Business error {body["status_code"]}: {body.get("status_message")}'
    return body.get('data')


@pytest.fixture(scope='module')
def client():
    with httpx.Client(timeout=60) as c:
        assert c.get(f'{API_BASE}/env').status_code == 200, 'Backend not reachable at ' + API_BASE
        yield c


@pytest.fixture(scope='module')
def admin_token(client):
    return _login(client, 'admin', os.environ.get('E2E_ADMIN_PASSWORD', 'admin123'))


@pytest.fixture(scope='module', autouse=True)
def lifecycle(client, admin_token):
    """Build: root → PREFIXparent → PREFIXchild, with a user that is a member of
    the CHILD only. The child's chain is therefore {root, parent, child}."""
    headers = _auth(admin_token)
    suffix = uuid.uuid4().hex[:6]

    # Clean leftovers from a previous run (deepest-first: a department cannot be
    # archived while it still has active children).
    found = _ok(
        client.get(
            f'{API_BASE}/departments/search',
            headers=headers,
            params={'keyword': PREFIX, 'include_archived': 'true', 'limit': 200},
        )
    )
    nodes = []

    def _flatten(items):
        for node in items or []:
            if str(node.get('name', '')).startswith(PREFIX):
                nodes.append(node)
            _flatten(node.get('children'))

    _flatten(found.get('roots', []) if isinstance(found, dict) else (found or []))
    for node in sorted(nodes, key=lambda n: len(str(n.get('path', ''))), reverse=True):
        dept_id = node.get('dept_id')
        if dept_id:
            client.delete(f'{API_BASE}/departments/{dept_id}', headers=headers)
            client.delete(f'{API_BASE}/departments/{dept_id}/purge', headers=headers)

    roots = _ok(client.get(f'{API_BASE}/departments/children', headers=headers, params={})) or []
    root_id = int(roots[0]['id']) if roots else 1

    parent = _ok(
        client.post(f'{API_BASE}/departments/', json={'name': f'{PREFIX}p-{suffix}', 'parent_id': root_id}, headers=headers)
    )
    child = _ok(
        client.post(
            f'{API_BASE}/departments/',
            json={'name': f'{PREFIX}c-{suffix}', 'parent_id': int(parent['id'])},
            headers=headers,
        )
    )

    user_name = f'{PREFIX}user-{suffix}'
    user_id = int(
        _ok(
            client.post(
                f'{API_BASE}/user/create',
                json={'user_name': user_name, 'password': _encrypt_password(client, PASSWORD)},
                headers=headers,
            )
        )['user_id']
    )
    _ok(
        client.post(
            f'{API_BASE}/departments/{child["dept_id"]}/members',
            json={'user_ids': [user_id], 'is_primary': 0},
            headers=headers,
        )
    )

    STATE.update(
        {
            'parent': parent,
            'child': child,
            'user_name': user_name,
            'user_id': user_id,
            'member_token': _login(client, user_name),
        }
    )

    yield STATE

    # Teardown — member first, or the child department cannot be archived.
    try:
        client.delete(f'{API_BASE}/departments/{child["dept_id"]}/members/{user_id}', headers=headers)
    except Exception:
        pass
    for dept in (child, parent):
        try:
            client.delete(f'{API_BASE}/departments/{dept["dept_id"]}', headers=headers)
            client.delete(f'{API_BASE}/departments/{dept["dept_id"]}/purge', headers=headers)
        except Exception:
            pass


def test_management_endpoint_still_refuses_a_plain_member(client):
    """The contrast case: `/departments/*` is management-scoped and must keep
    answering 21009 for an ordinary member.

    This is asserted deliberately. If the management family ever started
    answering ordinary members, the member-scoped endpoint would be unnecessary —
    and if it stopped refusing because someone loosened its scope, that would be
    a permission widening this suite should catch.
    """
    resp = client.get(f'{API_BASE}/departments/children', headers=_auth(STATE['member_token']))
    assert resp.json()['status_code'] == 21009, (
        'the management endpoint no longer refuses a plain member — either the '
        f'scope was widened, or the fixture user is unexpectedly an admin: {resp.json()}'
    )


def _own_chain(client, token: str) -> set[int]:
    """Every id reachable by walking the member's chain layer by layer.

    The endpoint is per-layer (like `/departments/children`), so the root layer
    legitimately contains only the chain's root — every ancestor between it and
    the member's own department has to be walked into via `parent_id`.
    """
    seen: set[int] = set()
    layer = _ok(client.get(f'{API_BASE}/knowledge/my_departments', headers=_auth(token))) or []

    for _ in range(10):  # bounded: the fixture chain is shallow
        if not layer:
            break
        seen.update(int(n['id']) for n in layer)
        nxt = [n for n in layer if n.get('has_children')]
        if not nxt:
            break
        layer = (
            _ok(
                client.get(
                    f'{API_BASE}/knowledge/my_departments',
                    headers=_auth(token),
                    params={'parent_id': int(nxt[0]['id'])},
                )
            )
            or []
        )
    return seen


def test_member_sees_own_department_chain(client):
    """AC: the caller's own department AND every ancestor are reachable.

    Both are filter targets: the member's department owns libraries they can see,
    and so does each ancestor — ownership flows downward, so an ancestor's
    library is visible to this member. A chain missing the ancestors would hide
    libraries the member legitimately has access to.
    """
    seen = _own_chain(client, STATE['member_token'])
    assert int(STATE['child']['id']) in seen, f'the member department is missing from its own chain: {seen}'
    assert int(STATE['parent']['id']) in seen, f'the ancestor department is missing from the chain: {seen}'
    # The root is an ancestor too, and it owns libraries the member can see.
    assert 1 in seen, f'the chain root is missing: {seen}'


def test_chain_is_walkable_layer_by_layer(client):
    """Walking parent_id down from the root layer reaches the member department.

    The tree component loads one layer at a time, so a chain that cannot be
    walked is unusable even if the ids are right.
    """
    token = STATE['member_token']
    seen: set[int] = set()
    layer = _ok(client.get(f'{API_BASE}/knowledge/my_departments', headers=_auth(token))) or []

    for _ in range(10):  # bounded: the chain is shallow by construction
        if not layer:
            break
        for node in layer:
            seen.add(int(node['id']))
        nxt = [n for n in layer if n.get('has_children')]
        if not nxt:
            break
        layer = (
            _ok(
                client.get(
                    f'{API_BASE}/knowledge/my_departments',
                    headers=_auth(token),
                    params={'parent_id': int(nxt[0]['id'])},
                )
            )
            or []
        )

    assert int(STATE['child']['id']) in seen, (
        f'the member department was not reachable by walking the chain: saw {seen}'
    )


def test_unrelated_departments_are_absent(client):
    """A department the member does not belong to, and that is not an ancestor of
    one, must not appear — the chain is membership-derived, not the whole tree."""
    data = _ok(client.get(f'{API_BASE}/knowledge/my_departments', headers=_auth(STATE['member_token'])))
    # The guest department (BS@guest, id=2) sits on a sibling branch that no chain
    # in this fixture passes through, so it must never appear.
    ids = {int(n['id']) for n in data}
    assert 2 not in ids, f'guest department leaked into a member chain: {data}'
