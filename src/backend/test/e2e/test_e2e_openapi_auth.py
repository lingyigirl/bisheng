"""F076 开放 API 鉴权 — e2e(真后端 + 真中间件)。

前置条件(缺一就跳过,并在 skip 原因里写清楚):
1. 后端在跑(`E2E_API_BASE`,默认 `http://localhost:7860/api/v1`);
2. **部署总闸 `open_api.enforce_keys: true`**(配置文件 + 重启)——总闸关时 `/api/v2`
   完全旧行为,本套用例无从验证,因此整模块跳过;
3. 管理员口令:`E2E_ADMIN_PASSWORD`(本机 dev 见运维记录)。

约定(对齐 /e2e-test 规范):
- 数据前缀 `e2e-openapi-`;**双 cleanup**(setup 清上次残留 + finally 清本次,失败有证据);
- **每个 allow 配一个 deny**;118xx 断言**真 HTTP 状态 + 信封业务码**两个都要对;
- 租户级开关由本套件临时打开、结束还原(匿名/guest 面的用例需要它)。
"""

from __future__ import annotations

import base64
import os
import time
import uuid

import httpx
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import padding

API_BASE = os.environ.get("E2E_API_BASE", "http://localhost:7860/api/v1")
V2_BASE = API_BASE.replace("/api/v1", "/api/v2")
HOST = API_BASE.replace("/api/v1", "")
PREFIX = "e2e-openapi-"
ACCOUNT_NAME = f"{PREFIX}{uuid.uuid4().hex[:8]}"

pytestmark = [pytest.mark.e2e]


def _login(client: httpx.Client, username: str, password: str) -> str:
    """同步 RSA 登录(helpers/auth.py 是纯异步版,这里镜像同一流程)。"""
    pk = client.get(f"{API_BASE}/user/public_key").json()["data"]["public_key"]
    public_key = serialization.load_pem_public_key(pk.encode())
    password_enc = base64.b64encode(public_key.encrypt(password.encode(), padding.PKCS1v15())).decode()
    resp = client.post(f"{API_BASE}/user/login", json={"user_name": username, "password": password_enc})
    body = resp.json()
    assert body.get("status_code") == 200, f"login failed: {body}"
    return body["data"]["access_token"]


def _admin_password() -> str:
    return os.environ.get("E2E_ADMIN_PASSWORD") or "admin123"


def _bearer(key: str) -> dict:
    return {"Authorization": f"Bearer {key}"}


def _expect(resp: httpx.Response, http_status: int, biz_code: int) -> dict:
    """真 HTTP 状态 + 信封业务码,两个都断(118xx 走真状态,assert_resp_error 不够)。"""
    body = resp.json() if resp.headers.get("content-type", "").startswith("application/json") else {}
    assert resp.status_code == http_status, f"http {resp.status_code} != {http_status}: {str(resp.text)[:200]}"
    assert body.get("status_code") == biz_code, f"biz {body.get('status_code')} != {biz_code}: {str(body)[:200]}"
    return body


class E2EState:
    """一次测试运行里共享的状态(管理员令牌 / 账号 / 密钥)。"""

    def __init__(self) -> None:
        self.token = ""
        self.account_id: int | None = None
        self.keys: dict[str, str] = {}
        self.tenant_switch_restore: bool | None = None


@pytest.fixture(scope="module")
def client():
    with httpx.Client(timeout=60) as c:
        assert c.get(f"{API_BASE}/env").status_code == 200, "Backend not reachable"
        yield c


@pytest.fixture(scope="module")
def state(client) -> E2EState:
    st = E2EState()
    st.token = _login(client, "admin", _admin_password())
    headers = {"Authorization": f"Bearer {st.token}"}

    settings_resp = client.get(f"{API_BASE}/open-api/settings", headers=headers)
    assert settings_resp.status_code == 200, settings_resp.text
    if not settings_resp.json()["data"].get("deployment_enforced"):
        pytest.skip("open_api.enforce_keys 未开启(部署总闸关=完全旧行为)——请在配置文件打开并重启后端后重跑本套件")

    # 双 cleanup 之"清上次残留":按前缀删掉上次跑剩的服务账号。
    _cleanup_accounts(client, headers)
    st.account_id = client.post(
        f"{API_BASE}/service-accounts", headers=headers, json={"name": ACCOUNT_NAME, "description": "e2e"}
    ).json()["data"]["id"]
    try:
        yield st
    finally:
        _cleanup_accounts(client, headers)


def _cleanup_accounts(client: httpx.Client, headers: dict) -> None:
    """按前缀清理服务账号(删除会连带吊销密钥/停用专用身份);失败留证据不吞。"""
    evidence: list[str] = []
    listing = client.get(f"{API_BASE}/service-accounts", headers=headers, params={"page": 1, "page_size": 200})
    if listing.status_code != 200:
        evidence.append(f"list failed: http={listing.status_code}")
    else:
        for row in listing.json()["data"]["data"]:
            if row["name"].startswith(PREFIX):
                resp = client.delete(f"{API_BASE}/service-accounts/{row['id']}", headers=headers)
                if resp.status_code != 200:
                    evidence.append(f"delete {row['id']} failed: http={resp.status_code} {resp.text[:120]}")
    if evidence:
        pytest.fail("cleanup failed:\n" + "\n".join(evidence))


def _issue_key(
    client: httpx.Client, state: E2EState, *, name: str, scopes: list[str], expires_at: str | None = None
) -> str:
    resp = client.post(
        f"{API_BASE}/service-accounts/{state.account_id}/keys",
        headers={"Authorization": f"Bearer {state.token}"},
        json={"name": name, "scopes": scopes, "expires_at": expires_at},
    )
    body = _expect(resp, 200, 200)
    return body["data"]["plaintext"]


def _admin_headers(state: E2EState) -> dict:
    return {"Authorization": f"Bearer {state.token}"}


@pytest.fixture(scope="module")
def tenant_switch(client, state):
    """把默认租户的强制开关打开(匿名面用例需要),结束后还原。"""
    resp = client.put(f"{API_BASE}/open-api/settings", headers=_admin_headers(state), json={"enforce_keys": True})
    assert resp.status_code == 200, resp.text
    yield True
    client.put(f"{API_BASE}/open-api/settings", headers=_admin_headers(state), json={"enforce_keys": False})


# ---------------------------------------------------------------------------
# AC-16/17/18:密钥校验的 allow / deny
# ---------------------------------------------------------------------------


def test_whoami_allow_and_deny(client, state, tenant_switch):
    key = _issue_key(client, state, name="whoami", scopes=["assistant:read"])
    allow = client.get(f"{V2_BASE}/auth/whoami", headers=_bearer(key))
    data = _expect(allow, 200, 200)
    assert data["data"]["key_mask"].startswith("bs-sak-********")
    assert data["data"]["scopes"] == ["assistant:read"]

    # deny:缺密钥(租户开关已开 → 匿名必拒)
    _expect(client.get(f"{V2_BASE}/auth/whoami"), 401, 11801)
    # deny:密钥无效
    _expect(client.get(f"{V2_BASE}/auth/whoami", headers=_bearer("bs-sak-" + "x" * 43)), 401, 11802)


def test_scope_allow_and_deny(client, state):
    key = _issue_key(client, state, name="scope", scopes=["chat:invoke"])
    # allow:chat:invoke 的端点在权限位内(会话标题对不存在的会话返回 New Chat)
    allow = client.post(f"{V2_BASE}/chat/gen_title", headers=_bearer(key), json={"conversationId": "e2e-none"})
    _expect(allow, 200, 200)
    # deny:同一把密钥调 assistant:read 的端点 → 403/11807 且回带缺失权限位
    deny = client.get(f"{V2_BASE}/assistant/list", headers=_bearer(key))
    body = _expect(deny, 403, 11807)
    assert body["data"].get("required") == "assistant:read"


def test_revoked_key_is_rejected(client, state):
    key = _issue_key(client, state, name="revoke", scopes=["assistant:read"])
    key_id = None
    listing = client.get(f"{API_BASE}/service-accounts/{state.account_id}/keys", headers=_admin_headers(state)).json()
    for row in listing["data"]["data"]:
        if row["key_mask"].endswith(key[-4:]):
            key_id = row["id"]
    assert key_id, "issued key not found in list"
    _expect(client.get(f"{V2_BASE}/auth/whoami", headers=_bearer(key)), 200, 200)  # allow
    assert (
        client.post(
            f"{API_BASE}/service-accounts/{state.account_id}/keys/{key_id}/revoke", headers=_admin_headers(state)
        ).status_code
        == 200
    )
    _expect(client.get(f"{V2_BASE}/auth/whoami", headers=_bearer(key)), 401, 11804)  # deny


def test_expired_key_is_rejected(client, state):
    soon = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(time.time() + 2))
    key = _issue_key(client, state, name="expire", scopes=["assistant:read"], expires_at=soon)
    _expect(client.get(f"{V2_BASE}/auth/whoami", headers=_bearer(key)), 200, 200)  # allow(未到期)
    time.sleep(3)
    _expect(client.get(f"{V2_BASE}/auth/whoami", headers=_bearer(key)), 401, 11803)  # deny


def test_disabled_account_is_rejected(client, state):
    key = _issue_key(client, state, name="disabled", scopes=["assistant:read"])
    _expect(client.get(f"{V2_BASE}/auth/whoami", headers=_bearer(key)), 200, 200)  # allow
    assert (
        client.post(
            f"{API_BASE}/service-accounts/{state.account_id}/disable", headers=_admin_headers(state)
        ).status_code
        == 200
    )
    try:
        # deny:停用会先吊销全部密钥,所以这把密钥看到的是 11804(已吊销);11805 留给
        # "密钥有效但专用身份不可用"的路径。两者都是 401,AC-05 的语义已满足。
        deny = client.get(f"{V2_BASE}/auth/whoami", headers=_bearer(key))
        assert deny.status_code == 401, deny.text
        assert deny.json().get("status_code") in (11804, 11805), deny.text
    finally:
        assert (
            client.post(
                f"{API_BASE}/service-accounts/{state.account_id}/enable", headers=_admin_headers(state)
            ).status_code
            == 200
        )


# ---------------------------------------------------------------------------
# AC-23/AC-25:越权与身份收口
# ---------------------------------------------------------------------------


def test_workflow_stop_denies_ungranted_flow(client, state):
    """AC-23:目标应用不在授权范围 → 404/11809,与"不存在"不可区分。"""
    key = _issue_key(client, state, name="ac23", scopes=["workflow:invoke"])
    ungranted = uuid.uuid4().hex
    body = {"workflow_id": ungranted, "session_id": f"{uuid.uuid4().hex}_e2e"}
    deny = client.post(f"{V2_BASE}/workflow/stop", headers=_bearer(key), json=body)
    _expect(deny, 404, 11809)
    # 不存在的 id 也是同一个码(防枚举:两者不可区分)
    never = uuid.uuid4().hex
    deny2 = client.post(
        f"{V2_BASE}/workflow/stop", headers=_bearer(key), json={"workflow_id": never, "session_id": body["session_id"]}
    )
    _expect(deny2, 404, 11809)


def test_bare_user_id_is_rejected_in_query_and_body(client, state):
    """AC-25 / INV-23 ③:query 与 JSON body 里的裸 user_id 都要 400/11817。"""
    key = _issue_key(client, state, name="ac25", scopes=["assistant:read", "knowledge:read"])
    _expect(client.get(f"{V2_BASE}/assistant/list", headers=_bearer(key), params={"user_id": 1}), 400, 11817)
    _expect(
        client.post(
            f"{V2_BASE}/filelib/retrieve",
            headers=_bearer(key),
            json={"knowledge_base_ids": [], "query": "x", "user_id": 1},
        ),
        400,
        11817,
    )


def _pick_grantable_assistant(client, state) -> str:
    """挑一个真实助手并授予该服务账号:没有授权时一切资源都是 404,工具校验根本走不到。"""
    listing = client.get(f"{API_BASE}/assistant", headers=_admin_headers(state), params={"page_size": 1})
    items = listing.json().get("data", {}).get("data") or []
    if not items:
        pytest.skip("no assistant in this deployment to grant")
    assistant_id = str(items[0]["id"])
    granted = client.post(
        f"{API_BASE}/service-accounts/{state.account_id}/resource-grants:mutate",
        headers=_admin_headers(state),
        json={"grants": [{"resource_type": "assistant", "resource_id": assistant_id, "level": "viewer"}], "revokes": []},
    )
    assert granted.status_code == 200, granted.text
    return assistant_id


def test_assistant_visibility_allow_and_deny(client, state):
    """AC-23 allow/deny:授权后可见(200);未授权的助手一律 404/11809(与不存在不可区分)。"""
    key = _issue_key(client, state, name="vis", scopes=["assistant:read"])
    granted_id = _pick_grantable_assistant(client, state)

    _expect(client.get(f"{V2_BASE}/assistant/info/{granted_id}", headers=_bearer(key)), 200, 200)

    deny = client.get(f"{V2_BASE}/assistant/info/{uuid.uuid4().hex}", headers=_bearer(key))
    _expect(deny, 404, 11809)


def test_filelib_retrieve_masks_ungranted_and_missing(client, state):
    """AC-23 在知识库面同样成立:未授权与不存在返回同一个 404/11809。

    T-29 追加修复:该端点原先把业务错误包成 HTTP 200 信封直接返回,异常不传播、
    中央映射不生效,对接方可按错误码区分"不存在"与"未授权"(可枚举)。
    """
    key = _issue_key(client, state, name="kb", scopes=["knowledge:read"])

    missing = client.post(
        f"{V2_BASE}/filelib/retrieve",
        headers=_bearer(key),
        json={"knowledge_base_ids": [999_999_999], "query": "e2e", "top_k": 1},
    )
    missing_body = _expect(missing, 404, 11809)

    # 真实存在但未授予该账号的知识库:同一个码、同一个状态(不可区分)
    listing = client.get(f"{API_BASE}/knowledge", headers=_admin_headers(state), params={"page_size": 1})
    items = listing.json().get("data", {}).get("data") or []
    if items:
        ungranted = client.post(
            f"{V2_BASE}/filelib/retrieve",
            headers=_bearer(key),
            json={"knowledge_base_ids": [int(items[0]["id"])], "query": "e2e", "top_k": 1},
        )
        ungranted_body = _expect(ungranted, 404, 11809)
        assert ungranted_body.get("status_code") == missing_body.get("status_code")


def test_unknown_tool_is_rejected(client, state):
    """AC-31(工具白名单 deny):已授权助手上指定未绑定的 tool_key → 403/11815(不静默忽略)。"""
    key = _issue_key(client, state, name="tools", scopes=["assistant:invoke"])
    granted_id = _pick_grantable_assistant(client, state)
    resp = client.post(
        f"{V2_BASE}/assistant/chat/completions",
        headers=_bearer(key),
        json={
            "model": granted_id,
            "messages": [{"role": "user", "content": "hi"}],
            "stream": False,
            "tools": [{"id": 1, "tool_key": "e2e-not-bound", "type": "tool"}],
        },
    )
    body = resp.json()
    assert body.get("status_code") == 11815, str(body)[:200]
    assert body["data"].get("tool_key") == "e2e-not-bound"


# ---------------------------------------------------------------------------
# AC-36:审计落库(直查库,验证一次调用一行)
# ---------------------------------------------------------------------------


def test_call_is_audited(client, state):
    import asyncio

    from sqlalchemy import text

    from bisheng.core.database import get_async_db_session

    key = _issue_key(client, state, name="audit", scopes=["assistant:read"])
    _expect(client.get(f"{V2_BASE}/auth/whoami", headers=_bearer(key)), 200, 200)
    time.sleep(3)  # 审计是批量落库(窗口 2s)

    async def _fetch() -> list[dict]:
        async with get_async_db_session() as session:
            result = await session.execute(
                text(
                    "select action, target_id, metadata from auditlog"
                    " where action = 'open_api.call' and metadata like :mask order by create_time desc limit 5"
                ),
                {"mask": f"%{key[-4:]}%"},
            )
            return [dict(row._mapping) for row in result.fetchall()]

    rows = asyncio.run(_fetch())
    assert rows, "no open_api.call audit row for the key just used"
    assert rows[0]["target_id"] == str(state.account_id)
