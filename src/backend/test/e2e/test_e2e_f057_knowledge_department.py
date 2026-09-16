"""F057 e2e: department ownership of organization knowledge libraries.

Real backend + middleware required (localhost:7860, override via E2E_API_BASE).
The suite drives the real HTTP chain: create departments → create libraries →
assign ownership → assert list visibility per role → archive the department and
assert the libraries fall back to unclassified.

Data isolation: every resource is ``e2e-f057-`` prefixed. The module-scoped
fixture cleans leftovers from a previous run first, and tears down only what it
created. Nothing outside the prefix is ever touched.

Why the users are real logins rather than mocked contexts: AC-01/AC-02/AC-03 are
about *who can see what*, and the read-time rule resolves the current user's
department chain from `UserDepartment` rows. A mocked login would prove nothing
about the chain the real request builds.
"""

import os
import uuid

import httpx
import pytest

from test.e2e.helpers.api import API_BASE

PREFIX = 'e2e-f057-'
PASSWORD = 'Test_pass_123'

# KnowledgeTypeEnum — organization libraries are NORMAL(0) / QA(1); SPACE and
# PRIVATE are explicitly out of scope for ownership.
#
# The numbers are spelled out rather than derived so the fixture reads plainly,
# which is exactly how they drifted: PRIVATE is 2 and SPACE is 3, NOT the other
# way round. `TYPE_SPACE` was pinned to 2, so the "space library is not
# assignable" case was really exercising a PRIVATE library and AC-21's actual
# claim went unverified end-to-end. Cross-check against
# `knowledge/domain/models/knowledge.py::KnowledgeTypeEnum` before changing.
TYPE_NORMAL = 0
TYPE_QA = 1
TYPE_PRIVATE = 2
TYPE_SPACE = 3

_STATE: dict = {}


# ---------------------------------------------------------------------------
# Auth / HTTP plumbing (mirrors test_e2e_department_space_scope.py)
# ---------------------------------------------------------------------------


def _encrypt_password(client: httpx.Client, password: str) -> str:
    import base64

    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import padding

    pem = client.get(f'{API_BASE}/user/public_key').json()['data']['public_key']
    public_key = serialization.load_pem_public_key(pem.encode())
    encrypted = public_key.encrypt(password.encode(), padding.PKCS1v15())
    return base64.b64encode(encrypted).decode()


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


def _create_user(client: httpx.Client, token: str, name: str) -> int:
    """Create a test user WITHOUT a group membership.

    `group_roles` is optional at the endpoint (`for one in req.group_roles or []`
    in `user/api/user.py`). Omitting it is deliberate: on a database whose
    `group` table is empty — which this shared dev DB is — supplying any
    `group_id` fails with `usergroup_ibfk_2` FK violation, and none of the F057
    assertions depend on group membership anyway. Visibility is resolved from
    `UserDepartment` rows, which `_add_member` writes below.

    The role falls back to `DefaultRole` server-side when `role_ids` is empty,
    so the user is a plain (non-admin) member — which is what AC-17 needs.
    """
    resp = client.post(
        f'{API_BASE}/user/create',
        json={
            'user_name': name,
            'password': _encrypt_password(client, PASSWORD),
        },
        headers=_auth(token),
    )
    return int(_ok(resp)['user_id'])


def _create_dept(client: httpx.Client, token: str, name: str, parent_id: int) -> dict:
    resp = client.post(
        f'{API_BASE}/departments/',
        json={'name': name, 'parent_id': parent_id},
        headers=_auth(token),
    )
    return _ok(resp)


def _add_member(client: httpx.Client, token: str, dept_id: str, user_id: int) -> None:
    _ok(
        client.post(
            f'{API_BASE}/departments/{dept_id}/members',
            json={'user_ids': [user_id], 'is_primary': 0},
            headers=_auth(token),
        )
    )


def _remove_member(client: httpx.Client, token: str, dept_id: str, user_id: int) -> None:
    client.delete(f'{API_BASE}/departments/{dept_id}/members/{user_id}', headers=_auth(token))


def _embedding_model_id(client: httpx.Client, token: str) -> int:
    """Any `embedding` model id — creation rejects an empty `model` with 10901.

    Resolved at runtime instead of hardcoded: model ids differ per deployment
    (the shared dev DB carries them 3 and 21), and a stale id would fail the
    whole suite for a reason unrelated to ownership.
    """
    # `_ok` unwraps the HTTP envelope's `data`, but this endpoint wraps its
    # payload once more via `_envelope(cfg, inherited, blocked)`, so the config
    # itself sits one level deeper.
    payload = _ok(client.get(f'{API_BASE}/llm/knowledge', headers=_auth(token)))
    cfg = payload.get('data', payload) if isinstance(payload, dict) else {}
    model_id = cfg.get('embedding_model_id') if isinstance(cfg, dict) else None
    assert model_id, f'no embedding model configured for knowledge: {str(payload)[:200]}'
    return int(model_id)


def _create_knowledge(
    client: httpx.Client, token: str, name: str, type_: int = TYPE_NORMAL, model_id: int | None = None
) -> int:
    payload = {'name': name, 'type': type_, 'description': 'F057 e2e'}
    if model_id is not None:
        payload['model'] = str(model_id)
    resp = client.post(f'{API_BASE}/knowledge/create', json=payload, headers=_auth(token))
    return int(_ok(resp)['id'])


def _delete_knowledge(client: httpx.Client, token: str, knowledge_id: int) -> None:
    """DELETE /knowledge/ takes its payload in the BODY (httpx: `content=`)."""
    import json as _json

    client.request(
        'DELETE',
        f'{API_BASE}/knowledge/',
        content=_json.dumps({'knowledge_id': knowledge_id}),
        headers=_auth(token),
    )


def _assign_department(client: httpx.Client, token: str, knowledge_id: int, department_id):
    return client.put(
        f'{API_BASE}/knowledge/department',
        json={'knowledge_id': knowledge_id, 'department_id': department_id},
        headers=_auth(token),
    )


def _list_library_ids(client: httpx.Client, token: str, **params) -> list:
    data = _ok(client.get(f'{API_BASE}/knowledge', params=params or None, headers=_auth(token)))
    rows = data.get('data', []) if isinstance(data, dict) else data
    return [int(r['id']) for r in rows]


def _list_department_ids(client: httpx.Client, token: str, **params) -> list:
    data = _ok(client.get(f'{API_BASE}/knowledge', params=params or None, headers=_auth(token)))
    rows = data.get('data', []) if isinstance(data, dict) else data
    return [r.get('department_id') for r in rows]


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
    """Build the org tree + libraries once; tear everything down afterwards.

    Layout (all names prefixed, so a stale run is recognizable):

        root
        └── A                 (owns lib_a)
            └── B             (owns lib_b)   — user_b is a member of B
        └── C                 (sibling of A) — user_c is a member of C
    """
    headers = _auth(admin_token)

    # --- cleanup leftovers from an earlier run -----------------------------
    for stale in _ok(client.get(f'{API_BASE}/knowledge', params={'name': PREFIX}, headers=headers)).get('data', []):
        if str(stale.get('name', '')).startswith(PREFIX):
            _delete_knowledge(client, admin_token, int(stale['id']))

    roots = _ok(client.get(f'{API_BASE}/departments/children', headers=headers, params={})) or []
    root_id = int(roots[0]['id']) if roots else 1

    def _purge_prefixed():
        """Hard-delete leftover prefixed departments, deepest-first.

        Two subtleties, both learned the hard way:
          * `GET /departments/children` returns ACTIVE nodes only, so an earlier
            run's archived leftovers are invisible there — they accumulate
            silently. `GET /departments/search?include_archived=true` is the one
            listing that surfaces them.
          * `DELETE /{dept_id}` archives (and refuses while active children
            exist); `DELETE /{dept_id}/purge` is the hard delete and requires the
            row to be archived first. So: archive deepest-first, then purge.
        """
        found = _ok(
            client.get(
                f'{API_BASE}/departments/search',
                headers=headers,
                params={'keyword': PREFIX, 'include_archived': 'true', 'limit': 200},
            )
        )
        # The search endpoint returns `{"roots": [...]}` — a pruned match tree,
        # NOT a flat list and NOT wrapped in `data`. Reading the wrong key here
        # silently yields nothing, and the cleanup appears to run while leaving
        # every department behind.
        roots = found.get('roots', []) if isinstance(found, dict) else (found or [])
        nodes = []

        def _flatten(items):
            for node in items or []:
                if str(node.get('name', '')).startswith(PREFIX):
                    nodes.append(node)
                _flatten(node.get('children'))

        _flatten(roots)

        # Deepest-first: longer `path` means deeper. Children must go before
        # their parent, because the archive precondition refuses a department
        # that still has ACTIVE CHILDREN.
        for node in sorted(nodes, key=lambda n: len(str(n.get('path', ''))), reverse=True):
            dept_id = node.get('dept_id')
            if not dept_id:
                continue
            # ...and it equally refuses one that still has MEMBERS. Detaching
            # them is mandatory, not tidiness: this suite's own fixtures remove
            # their members, but a leftover from an EARLIER run still carries
            # its users, and every subsequent run then inherits a department it
            # can never archive. That is how 50+ orphans accumulated.
            try:
                members = _ok(
                    client.get(f'{API_BASE}/departments/{dept_id}/members', params={'page': 1, 'limit': 200}, headers=headers)
                )
                rows = members.get('data', members) if isinstance(members, dict) else (members or [])
                for m in rows or []:
                    uid = m.get('user_id')
                    if uid is not None:
                        client.delete(f'{API_BASE}/departments/{dept_id}/members/{uid}', headers=headers)
            except Exception:
                pass
            client.delete(f'{API_BASE}/departments/{dept_id}', headers=headers)
            client.delete(f'{API_BASE}/departments/{dept_id}/purge', headers=headers)

    _purge_prefixed()

    # --- build --------------------------------------------------------------
    suffix = uuid.uuid4().hex[:6]
    dept_a = _create_dept(client, admin_token, f'{PREFIX}tech-{suffix}', root_id)
    dept_b = _create_dept(client, admin_token, f'{PREFIX}rd-{suffix}', int(dept_a['id']))
    dept_c = _create_dept(client, admin_token, f'{PREFIX}hr-{suffix}', root_id)

    user_b_id = _create_user(client, admin_token, f'{PREFIX}user-b-{suffix}')
    user_c_id = _create_user(client, admin_token, f'{PREFIX}user-c-{suffix}')
    _add_member(client, admin_token, dept_b['dept_id'], user_b_id)
    _add_member(client, admin_token, dept_c['dept_id'], user_c_id)

    model_id = _embedding_model_id(client, admin_token)
    lib_a = _create_knowledge(client, admin_token, f'{PREFIX}lib-a-{suffix}', TYPE_NORMAL, model_id)
    lib_b = _create_knowledge(client, admin_token, f'{PREFIX}lib-b-{suffix}', TYPE_NORMAL, model_id)
    lib_qa = _create_knowledge(client, admin_token, f'{PREFIX}lib-qa-{suffix}', TYPE_QA, model_id)
    lib_space = _create_knowledge(client, admin_token, f'{PREFIX}lib-space-{suffix}', TYPE_SPACE, model_id)
    # An ordinary library owned by NO department. The filter tests need a
    # discriminator that is the SAME type as the filtered rows (type=0) and
    # unowned — a type=3 library is absent from a type=0 list regardless of
    # whether the department filter works, so it cannot prove the filter narrows.
    lib_unowned = _create_knowledge(client, admin_token, f'{PREFIX}lib-unowned-{suffix}', TYPE_NORMAL, model_id)

    _STATE.update(
        {
            'dept_a': dept_a,
            'dept_b': dept_b,
            'dept_c': dept_c,
            'user_b_id': user_b_id,
            'user_c_id': user_c_id,
            'user_b_token': _login(client, f'{PREFIX}user-b-{suffix}', PASSWORD),
            'user_c_token': _login(client, f'{PREFIX}user-c-{suffix}', PASSWORD),
            'lib_a': lib_a,
            'lib_b': lib_b,
            'lib_qa': lib_qa,
            'lib_space': lib_space,
            'lib_unowned': lib_unowned,
        }
    )

    yield _STATE

    # --- teardown: only what we made ---------------------------------------
    for key in ('lib_a', 'lib_b', 'lib_qa', 'lib_space', 'lib_unowned'):
        try:
            _delete_knowledge(client, admin_token, _STATE[key])
        except Exception:
            pass

    # Members must go before their department can be archived (the archive
    # precondition refuses a department that still has members).
    for user_key, dept_key in (('user_b_id', 'dept_b'), ('user_c_id', 'dept_c')):
        try:
            dept = _STATE.get(dept_key)
            if dept:
                _remove_member(client, admin_token, dept['dept_id'], _STATE[user_key])
        except Exception:
            pass

    # Sweep every prefixed department, archived ones included. The individual
    # deletes above are best-effort; this is what actually guarantees the
    # environment is left clean even when a test failed midway.
    try:
        _purge_prefixed()
    except Exception:
        pass

    # Finally the users this suite created. They are what make a re-run costly:
    # leaving them behind means each run adds fresh accounts to a shared DB.
    try:
        users = _ok(client.get(f'{API_BASE}/user/list', params={'page': 1, 'limit': 200}, headers=headers))
        rows = users.get('data', users) if isinstance(users, dict) else users
        for row in rows or []:
            if str(row.get('user_name', '')).startswith(PREFIX):
                client.post(
                    f'{API_BASE}/user/delete',
                    json={'user_id': int(row['user_id'])},
                    headers=headers,
                )
    except Exception:
        pass


# ---------------------------------------------------------------------------
# AC-17 ~ AC-21: assignment is privilege-managed
# ---------------------------------------------------------------------------


def test_ac17_assignment_requires_manage_permission(client, admin_token):
    """AC-17: 无管理权限的操作者不能设置或取消归属。"""
    resp = _assign_department(client, _STATE['user_b_token'], _STATE['lib_a'], _STATE['dept_a']['id'])
    assert resp.status_code == 200
    assert resp.json()['status_code'] == 403, (
        f"a plain member changed ownership: {resp.json().get('status_message')}"
    )


def test_ac19_admin_can_clear_ownership(client, admin_token):
    """AC-19: 取消归属后该库回到未分类, 可见性退回既有 ReBAC 口径。"""
    _ok(_assign_department(client, admin_token, _STATE['lib_a'], _STATE['dept_a']['id']))
    cleared = _ok(_assign_department(client, admin_token, _STATE['lib_a'], None))
    assert cleared is None or cleared == {}

    rows = _ok(
        client.get(
            f'{API_BASE}/knowledge',
            params={'name': f'{PREFIX}lib-a'},
            headers=_auth(admin_token),
        )
    )['data']
    row = next(r for r in rows if int(r['id']) == _STATE['lib_a'])
    assert row.get('department_id') is None


def test_ac20_unknown_target_department_rejected(client, admin_token):
    """AC-20: 目标部门不存在 → 拒绝指派。"""
    resp = _assign_department(client, admin_token, _STATE['lib_a'], 999_999_999)
    assert resp.status_code == 200
    assert resp.json()['status_code'] == 403


def test_ac21_space_library_is_not_assignable(client, admin_token):
    """AC-21: 知识空间不可指派 (它有自己的绑定表)。"""
    resp = _assign_department(client, admin_token, _STATE['lib_space'], _STATE['dept_a']['id'])
    assert resp.status_code == 200
    assert resp.json()['status_code'] == 403, (
        f"knowledge_space accepted an ownership assignment: {resp.json().get('status_message')}"
    )


def test_ac21_qa_library_is_assignable(client, admin_token):
    """AC-21 (正面): 问答库可指派 — 只有知识空间与个人库被排除。"""
    _ok(_assign_department(client, admin_token, _STATE['lib_qa'], _STATE['dept_a']['id']))


# ---------------------------------------------------------------------------
# AC-01 ~ AC-03, AC-24: read-time visibility and its direction
# ---------------------------------------------------------------------------


def test_ac01_ac02_ownership_assigned_for_visibility(client, admin_token):
    """AC-01/AC-02 前置: 把 lib_b 指派给部门 B (AC-01/02 的断言在后续用例)。"""
    _ok(_assign_department(client, admin_token, _STATE['lib_b'], _STATE['dept_b']['id']))


def test_ac01_member_of_owning_department_sees_library(client, admin_token):
    """AC-01: 归属部门 B 的成员可以看到 lib_b。"""
    assert _STATE['lib_b'] in _list_library_ids(client, _STATE['user_b_token'], name=PREFIX)


def test_ac02_descendant_member_sees_ancestor_library(client, admin_token):
    """AC-02: 归属传递向下 — B 是 A 的后代, 故 A 的库对 B 成员可见。

    这里反向验证同一读时规则: 若把 lib_a 指派给 A, 则 B 的成员应能看到它。
    """
    _ok(_assign_department(client, admin_token, _STATE['lib_a'], _STATE['dept_a']['id']))
    ids = _list_library_ids(client, _STATE['user_b_token'], name=PREFIX)
    assert _STATE['lib_a'] in ids, 'a descendant department lost the ancestor library'


def test_ac03_sibling_department_member_gets_nothing(client, admin_token):
    """AC-03: 方向守卫 — 兄弟部门 C 的成员看不到 A/B 的库。"""
    ids = _list_library_ids(client, _STATE['user_c_token'], name=PREFIX)
    assert _STATE['lib_a'] not in ids, 'sibling department saw a library it must not'
    assert _STATE['lib_b'] not in ids, 'sibling department saw a library it must not'


def test_ac24_dangling_department_grants_nothing(client, admin_token):
    """AC-24: 悬空归属 (部门不存在) 不产生可见性, 且列表不报错。

    Two halves: (a) a department that does not exist filters to nothing even
    though owned libraries exist, and (b) an owned library whose department row
    has vanished grants nobody visibility.

    (a) deliberately asserts against the module's OWN libraries via an exact
    owner filter — asserting only "unknown department → []" passes vacuously
    when the suite happens to own no libraries at all, which is how this test
    first went green for the wrong reason.
    """
    # (a) owned libraries DO exist in this module — otherwise the assertion below
    # is vacuous, so prove that first.
    owned = _list_library_ids(client, admin_token, name=f'{PREFIX}lib-')
    assert owned, 'module owns no libraries; the AC-24 assertion would be vacuous'

    ids = _list_library_ids(client, admin_token, name=f'{PREFIX}lib-', department_id=999_999_999)
    assert ids == [], f'an unknown department filter returned rows instead of an empty list: {ids}'

    # (b) a library whose owning department is gone (dangling id) is simply
    # unclassified — the permission path finds no department and grants nothing.
    # Exercised end-to-end by AC-22 (archive clears ownership); here we assert the
    # read side does not error on a stale filter value.
    stale = _list_library_ids(client, admin_token, name=f'{PREFIX}lib-', department_id=-1)
    assert stale == [], f'a negative/unknown department id returned rows: {stale}'


# ---------------------------------------------------------------------------
# AC-09 ~ AC-16: the platform browsing filter
# ---------------------------------------------------------------------------


def test_ac09_filter_narrows_to_department_subtree(client, admin_token):
    """AC-09: 按部门筛选只返回该部门及其下级部门的库。"""
    ids = _list_library_ids(client, admin_token, department_id=int(_STATE['dept_a']['id']), name=f'{PREFIX}lib-')
    assert _STATE['lib_a'] in ids, 'own library missing from its own department filter'
    assert _STATE['lib_b'] in ids, 'sub-department library missing from the subtree filter'
    # `lib_unowned` is type=0 and belongs to nobody, so it IS a candidate the
    # filter must exclude. (Its predecessor here was a type=3 library, which a
    # type=0 list never returns anyway — that assertion could not fail.)
    assert _STATE['lib_unowned'] not in ids, 'an unowned library leaked into the department filter'


def test_ac11_clearing_filter_restores_full_list(client, admin_token):
    """AC-11: 清除筛选后恢复为不按部门筛选的完整结果。

    Compares the department-C filter against the unfiltered list and requires
    the two to differ — department C owns nothing here, so its filter must be
    empty while the unfiltered list is not. That makes the assertion meaningful:
    if the filter were ignored entirely, both sides would be identical and this
    test fails rather than passing vacuously.
    """
    dept_c_id = int(_STATE['dept_c']['id'])
    filtered = set(_list_library_ids(client, admin_token, department_id=dept_c_id, name=f'{PREFIX}lib-'))
    unfiltered = set(_list_library_ids(client, admin_token, name=f'{PREFIX}lib-'))

    assert unfiltered, 'module owns no libraries; AC-11 would be vacuous'
    assert filtered != unfiltered, 'the filter had no effect, so AC-09 cannot be trusted'
    assert filtered.issubset(unfiltered)


def test_ac15_unknown_department_filter_is_empty_not_unfiltered(client, admin_token):
    """AC-15: 选中已不存在的部门 → 空结果, 绝不降级为「不筛选」。

    Must prove the unfiltered list is non-empty first — "unknown department → []"
    is trivially true on an empty tenant and would not detect the widening bug
    this AC exists to prevent.
    """
    unfiltered = _list_library_ids(client, admin_token, name=f'{PREFIX}lib-')
    assert unfiltered, 'module owns no libraries; AC-15 would be vacuous'

    ids = _list_library_ids(client, admin_token, department_id=999_999_999, name=f'{PREFIX}lib-')
    assert ids == [], f'a stale tree selection silently widened to the whole tenant: {ids}'


def test_ac16_admin_filter_also_narrows(client, admin_token):
    """AC-16: 管理员同样受部门筛选约束 (admin 分支曾静默丢弃该参数)。

    `lib_unowned` is the discriminator — an ordinary (type=0) library owned by NO
    department. It must appear in the unfiltered list and disappear under a
    department filter. Merely asserting "lib_b is present" would pass even if the
    filter were ignored, because an unnarrowed list also contains lib_b.

    The discriminator has to be the SAME type as the filtered rows: a type=3
    library is absent from a type=0 list whether or not the filter works, so it
    could never fail and would prove nothing.
    """
    base = {'type': TYPE_NORMAL, 'name': f'{PREFIX}lib-'}
    unfiltered = set(_list_library_ids(client, admin_token, **base))
    assert _STATE['lib_unowned'] in unfiltered, 'unowned library missing from the unfiltered list'

    ids = _list_library_ids(client, admin_token, department_id=int(_STATE['dept_a']['id']), **base)
    assert _STATE['lib_unowned'] not in ids, 'admin filter did not narrow: unowned library returned'

    # ...while the owned document library survives that same filter — which is
    # what proves the narrowing is a filter and not a blanket empty result.
    assert _STATE['lib_a'] in ids


# ---------------------------------------------------------------------------
# AC-12 ~ AC-14: badges and default selection
# ---------------------------------------------------------------------------


def test_ac12_department_stats_counts_owned_libraries(client, admin_token):
    """AC-12: 部门统计返回各节点 (含下级) 的库数量。"""
    data = _ok(
        client.get(
            f'{API_BASE}/knowledge/department_stats',
            params={'type': TYPE_NORMAL},
            headers=_auth(admin_token),
        )
    )
    by_dept = {int(r['department_id']): int(r['count']) for r in (data or [])}
    dept_a_id = int(_STATE['dept_a']['id'])
    assert by_dept.get(dept_a_id, 0) >= 1, f'dept A has no badge count: {by_dept}'
    # Subtree-inclusive: A's count covers A's own libraries AND its descendants'.
    direct_a = len(
        [
            k
            for k in _list_department_ids(client, admin_token, department_id=dept_a_id, name=PREFIX)
            if k is not None
        ]
    )
    assert by_dept[dept_a_id] >= direct_a


def test_ac14_primary_department_is_returned_for_caller(client, admin_token):
    """AC-14: 不传 user_id 时返回调用者自己的主部门 (前端据此预选树节点)。"""
    data = _ok(client.get(f'{API_BASE}/departments/get_user_primary_department', headers=_auth(admin_token)))
    assert isinstance(data, list), f'expected a list, got {type(data)}'


# ---------------------------------------------------------------------------
# AC-22 / AC-23: department removal unclassifies its libraries
# ---------------------------------------------------------------------------


def test_ac22_archive_unclassifies_libraries(client, admin_token):
    """AC-22/AC-23: 部门归档后其名下知识库回到未分类。

    Archive requires zero members and no active children, so the department is
    built fresh here rather than reusing the shared tree.
    """
    headers = _auth(admin_token)
    suffix = uuid.uuid4().hex[:6]
    root_id = int(_ok(client.get(f'{API_BASE}/departments/children', headers=headers, params={}))[0]['id'])

    dept = _create_dept(client, admin_token, f'{PREFIX}temp-{suffix}', root_id)
    lib = _create_knowledge(
        client, admin_token, f'{PREFIX}lib-temp-{suffix}', TYPE_NORMAL, _embedding_model_id(client, admin_token)
    )
    _ok(_assign_department(client, admin_token, lib, dept['id']))

    before = _ok(
        client.get(f'{API_BASE}/knowledge', params={'name': f'{PREFIX}lib-temp-{suffix}'}, headers=headers)
    )['data']
    assert next(r for r in before if int(r['id']) == lib).get('department_id') == dept['id'], (
        'assignment did not persist, so the archive assertion would be vacuous'
    )

    _ok(client.delete(f'{API_BASE}/departments/{dept["dept_id"]}', headers=headers))

    after = _ok(
        client.get(f'{API_BASE}/knowledge', params={'name': f'{PREFIX}lib-temp-{suffix}'}, headers=headers)
    )['data']
    row = next(r for r in after if int(r['id']) == lib)
    assert row.get('department_id') is None, (
        f'archiving the department left the library owned: department_id={row.get("department_id")}'
    )

    _delete_knowledge(client, admin_token, lib)
