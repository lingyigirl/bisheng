"""F077 定时任务 — e2e（真后端 + 真中间件）。

覆盖两块**对外面**：管理 API（`/api/v1/scheduled-tasks`）与事件触发面
（`/api/v2/schedule/trigger`）。运行本身的调度语义由单测覆盖（`test_run_service`），
本套件不驱动 tick/beat——需要等待或消耗真实运行的事项显式 skip 并写清原因。

前置条件（缺一则整模块或单条 skip）：
1. 后端在跑（`E2E_API_BASE`，默认 `http://localhost:7860/api/v1`）；
2. 管理员口令 `E2E_ADMIN_PASSWORD`；
3. 至少有一个助手或工作流应用可被管理员看到（优先助手：创建任务不受"输入节点形状"
   限制，且运行成本最低）。**本套件会真实触发 1 次应用运行**。

约定（对齐 /e2e-test 规范）：数据前缀 `e2e-sched-`；双 cleanup（setup 清残留 +
finally 清本次）；每个 allow 配 deny；119xx 断言真 HTTP 状态 + 信封业务码。
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
PREFIX = "e2e-sched-"
SUFFIX = uuid.uuid4().hex[:8]
ACCOUNT_NAME = f"{PREFIX}{SUFFIX}"

pytestmark = [pytest.mark.e2e]


def _login(client: httpx.Client, username: str, password: str) -> str:
    pk = client.get(f"{API_BASE}/user/public_key").json()["data"]["public_key"]
    public_key = serialization.load_pem_public_key(pk.encode())
    enc = base64.b64encode(public_key.encrypt(password.encode(), padding.PKCS1v15())).decode()
    body = client.post(f"{API_BASE}/user/login", json={"user_name": username, "password": enc}).json()
    assert body.get("status_code") == 200, f"login failed: {body}"
    return body["data"]["access_token"]


def _admin_password() -> str:
    return os.environ.get("E2E_ADMIN_PASSWORD") or "admin123"


def _bearer(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _expect(resp: httpx.Response, http_status: int, biz_code: int) -> dict:
    body = resp.json() if resp.headers.get("content-type", "").startswith("application/json") else {}
    assert resp.status_code == http_status, f"http {resp.status_code} != {http_status}: {str(resp.text)[:200]}"
    assert body.get("status_code") == biz_code, f"biz {body.get('status_code')} != {biz_code}: {str(body)[:200]}"
    return body


def _trigger_headers(key: str, *, nonce: str | None = None, ts_offset: int = 0) -> dict:
    return {
        "Authorization": f"Bearer {key}",
        "X-Bs-Timestamp": str(int(time.time()) + ts_offset),
        "X-Bs-Nonce": nonce or uuid.uuid4().hex,
    }


class E2EState:
    def __init__(self) -> None:
        self.token = ""
        self.app_id: str = ""
        self.app_type: int = 5  # FlowType.ASSISTANT
        self.account_id: int | None = None
        self.key: str = ""
        self.readonly_key: str = ""
        self.task_id: int | None = None


def _pick_app(client: httpx.Client, headers: dict) -> tuple[str, int]:
    """优先助手（形状校验恒过、运行最轻）；没有就退到工作流。"""
    assistants = client.get(f"{API_BASE}/assistant", headers=headers, params={"page_size": 20}).json()
    items = (assistants.get("data") or {}).get("data") or []
    for item in items:
        if item.get("status") == 2:  # ONLINE
            return str(item["id"]), 5
    workflows = client.get(f"{API_BASE}/workflow/list", headers=headers, params={"page_size": 20}).json()
    for item in (workflows.get("data") or {}).get("data") or []:
        if item.get("status") == 2:
            return str(item["id"]), 10
    pytest.skip("环境里没有在线应用（助手/工作流），无法创建定时任务")


@pytest.fixture(scope="module")
def client():
    with httpx.Client(timeout=60) as c:
        try:
            ok = c.get(f"{API_BASE}/env").status_code == 200
        except Exception:
            ok = False
        assert ok, f"backend not reachable at {API_BASE}"
        yield c


@pytest.fixture(scope="module")
def state(client) -> E2EState:
    st = E2EState()
    st.token = _login(client, "admin", _admin_password())
    headers = _bearer(st.token)

    # 清上次残留（按名字前缀）
    listing = client.get(f"{API_BASE}/service-accounts", headers=headers, params={"keyword": PREFIX}).json()
    for account in (listing.get("data") or {}).get("data") or []:
        client.delete(f"{API_BASE}/service-accounts/{account['id']}", headers=headers)

    st.app_id, st.app_type = _pick_app(client, headers)

    account = client.post(
        f"{API_BASE}/service-accounts",
        headers=headers,
        json={"name": ACCOUNT_NAME, "description": "e2e scheduled triggers"},
    ).json()
    assert account.get("status_code") == 200, account
    st.account_id = account["data"]["id"]

    # 授权 editor 档（editor 才含 use_app —— 运行准入）
    grant = client.post(
        f"{API_BASE}/service-accounts/{st.account_id}/resource-grants:mutate",
        headers=headers,
        json={
            "grants": [
                {
                    "resource_type": "assistant" if st.app_type == 5 else "workflow",
                    "resource_id": st.app_id,
                    "level": "editor",
                }
            ],
            "revokes": [],
        },
    )
    assert grant.status_code == 200, grant.text

    key = client.post(
        f"{API_BASE}/service-accounts/{st.account_id}/keys",
        headers=headers,
        json={"name": "e2e-trigger", "scopes": ["schedule:trigger"]},
    ).json()
    assert key.get("status_code") == 200, key
    st.key = key["data"]["plaintext"]

    yield st

    # 双 cleanup：删任务 → 删账号（级联吊销密钥与授权）
    if st.task_id:
        client.delete(f"{API_BASE}/scheduled-tasks/{st.task_id}", headers=headers)
    if st.account_id:
        client.delete(f"{API_BASE}/service-accounts/{st.account_id}", headers=headers)


# ── 管理面 ───────────────────────────────────────────────────────────


def test_create_task_and_list(client, state):
    headers = _bearer(state.token)
    resp = client.post(
        f"{API_BASE}/scheduled-tasks",
        headers=headers,
        json={
            "name": f"{PREFIX}task-{SUFFIX}",
            "app_id": state.app_id,
            "app_type": state.app_type,
            "schedule_type": "cron",
            "cron_expression": "*/5 * * * *",
            "service_account_id": state.account_id,
        },
    )
    body = resp.json()
    assert body.get("status_code") == 200, body
    task = body["data"]
    state.task_id = task["id"]

    assert task["schedule_description"], "列表/详情应有可读的触发计划（AC-07）"
    assert task["next_run_at"], "创建后即应有下次执行时间"
    assert task["status"] == "enabled"

    listed = client.get(
        f"{API_BASE}/scheduled-tasks",
        headers=headers,
        params={"app_id": state.app_id, "app_type": state.app_type},
    ).json()
    assert any(row["id"] == state.task_id for row in listed["data"]["data"])


def test_available_identities_are_listed_without_secrets(client, state):
    headers = _bearer(state.token)
    resp = client.get(
        f"{API_BASE}/scheduled-tasks/available-identities",
        headers=headers,
        params={"app_id": state.app_id, "app_type": state.app_type},
    ).json()
    assert resp.get("status_code") == 200, resp
    item = next((i for i in resp["data"]["data"] if i["service_account_id"] == state.account_id), None)
    assert item is not None and item["usable"] is True, "刚授权的账号应出现在可用清单里"
    # 反证：不得回带密钥/凭据字段（AC-24）
    assert not ({"key", "plaintext", "secret", "token", "scopes"} & set(item.keys()))


# ── 事件触发面 ───────────────────────────────────────────────────────


def test_trigger_requires_key(client, state):
    _expect(
        client.post(f"{V2_BASE}/schedule/trigger", json={"task_id": state.task_id}),
        401,
        11801,
    )


def test_trigger_unknown_task_is_404(client, state):
    _expect(
        client.post(
            f"{V2_BASE}/schedule/trigger",
            headers=_trigger_headers(state.key),
            json={"task_id": 999_999_999},
        ),
        404,
        11809,
    )


def test_trigger_body_whitelist(client, state):
    _expect(
        client.post(
            f"{V2_BASE}/schedule/trigger",
            headers=_trigger_headers(state.key),
            json={"task_id": state.task_id, "app_id": state.app_id},
        ),
        400,
        11916,
    )


def test_trigger_replay_is_rejected(client, state):
    nonce = uuid.uuid4().hex
    first = client.post(
        f"{V2_BASE}/schedule/trigger",
        headers=_trigger_headers(state.key, nonce=nonce),
        json={"task_id": state.task_id},
    )
    assert first.status_code == 200, first.text
    _expect(
        client.post(
            f"{V2_BASE}/schedule/trigger",
            headers=_trigger_headers(state.key, nonce=nonce),
            json={"task_id": state.task_id},
        ),
        400,
        11912,
    )


def test_trigger_stale_timestamp_is_rejected(client, state):
    _expect(
        client.post(
            f"{V2_BASE}/schedule/trigger",
            headers=_trigger_headers(state.key, ts_offset=3600),
            json={"task_id": state.task_id},
        ),
        400,
        11912,
    )


def test_disabled_task_is_404_then_enabled_again(client, state):
    headers = _bearer(state.token)
    assert (
        client.post(f"{API_BASE}/scheduled-tasks/{state.task_id}/disable", headers=headers).json()["status_code"] == 200
    )
    _expect(
        client.post(
            f"{V2_BASE}/schedule/trigger",
            headers=_trigger_headers(state.key),
            json={"task_id": state.task_id},
        ),
        404,
        11809,
    )
    assert (
        client.post(f"{API_BASE}/scheduled-tasks/{state.task_id}/enable", headers=headers).json()["status_code"] == 200
    )


def test_run_now_returns_trace_or_conflict(client, state):
    """立即运行：受理回 trace，或与触发面并发时判 11909（两者都是合法结果）。

    注意口径：**v1 管理面**的 119xx 一律走既有信封（HTTP 200 + 业务码），只有
    `/api/v2` 面才回真 HTTP 状态（design 决策 15）——所以这里断的是 body 里的码。
    """
    headers = _bearer(state.token)
    resp = client.post(f"{API_BASE}/scheduled-tasks/{state.task_id}/run", headers=headers)
    body = resp.json()
    assert resp.status_code == 200, body
    if body.get("status_code") == 200:
        assert (body.get("data") or {}).get("run_trace_id"), body
    else:
        assert body.get("status_code") in (11909, 11910), body


def test_delete_task(client, state):
    headers = _bearer(state.token)
    assert client.delete(f"{API_BASE}/scheduled-tasks/{state.task_id}", headers=headers).json()["status_code"] == 200
    listed = client.get(
        f"{API_BASE}/scheduled-tasks", headers=headers, params={"app_id": state.app_id, "app_type": state.app_type}
    ).json()
    assert not any(row["id"] == state.task_id for row in listed["data"]["data"])
    state.task_id = None


# ── 已知未覆盖（需要资源或会污染共享环境，显式跳过并写明原因） ──────────


@pytest.mark.skip(
    reason="多租户隔离需要第二个租户与第二套管理员会话；单测 test_task_service 已覆盖租户过滤，现场联调按验收表手工验证"
)
def test_tenant_isolation():
    pass


@pytest.mark.skip(reason="连续失败自动停用需要让运行真实失败 5 次（消耗真实应用运行）；由验收探针在预发执行")
def test_auto_disable_after_consecutive_failures():
    pass


@pytest.mark.skip(
    reason="应用下线三档处置需要临时下线共享环境里的真实应用（会影响他人）；单测 test_run_service + 验收表覆盖"
)
def test_offline_app_dispositions():
    pass
