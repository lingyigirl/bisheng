"""F078 智能体纳管 — e2e（真后端 + 本地 mock 智能体）。

前置条件（缺一则整模块或单条 skip）：
1. 后端在跑（`E2E_API_BASE`，默认 `http://localhost:7860/api/v1`）；
2. 管理员口令 `E2E_ADMIN_PASSWORD`；
3. `managed_agent.enabled=true`；
4. `managed_agent.allow_private_networks=true` 且 allowlist 允许本地 mock 端口
   （仅测试环境；生产必须关闭）。

约定：数据前缀 `e2e-managed-`；双 cleanup；不驱动 beat（计划轮次语义由单测覆盖）。
"""

from __future__ import annotations

import base64
import json
import os
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import padding

API_BASE = os.environ.get("E2E_API_BASE", "http://localhost:7860/api/v1")
V2_BASE = API_BASE.replace("/api/v1", "/api/v2")
PREFIX = "e2e-managed-"
SUFFIX = uuid.uuid4().hex[:8]
AGENT_NAME = f"{PREFIX}agent-{SUFFIX}"
ACCOUNT_NAME = f"{PREFIX}account-{SUFFIX}"

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


class _MockHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        if self.path != "/v1/chat/completions":
            self.send_response(404)
            self.end_headers()
            return
        length = int(self.headers.get("content-length", 0))
        self.rfile.read(length)
        body = json.dumps({"choices": [{"message": {"content": "pong"}}], "usage": {"total_tokens": 1}}).encode()
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/health":
            self.send_response(200)
            self.end_headers()
            return
        self.send_response(404)
        self.end_headers()

    def log_message(self, *args):  # silence the test output
        return


class E2EState:
    def __init__(self) -> None:
        self.token = ""
        self.agent_id: int | None = None
        self.account_id: int | None = None
        self.key = ""
        self.task_id: int | None = None
        self.taxonomy_id: int | None = None
        self.mock_port = 0


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
def mock_server():
    server = ThreadingHTTPServer(("127.0.0.1", 0), _MockHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server.server_address[1]
    server.shutdown()


@pytest.fixture(scope="module")
def state(client, mock_server) -> E2EState:
    st = E2EState()
    st.mock_port = mock_server
    st.token = _login(client, "admin", _admin_password())
    headers = _bearer(st.token)

    probe = client.get(f"{API_BASE}/managed-agents", headers=headers, params={"page_size": 1})
    if probe.status_code == 404 or probe.json().get("status_code") not in (200,):
        pytest.skip("managed_agent.enabled is off or the feature is not mounted")

    # cleanup leftovers
    listing = client.get(f"{API_BASE}/managed-agents", headers=headers, params={"keyword": PREFIX}).json()
    for row in (listing.get("data") or {}).get("data") or []:
        client.delete(f"{API_BASE}/managed-agents/{row['id']}", headers=headers)
    accounts = client.get(f"{API_BASE}/service-accounts", headers=headers, params={"keyword": PREFIX}).json()
    for account in (accounts.get("data") or {}).get("data") or []:
        client.delete(f"{API_BASE}/service-accounts/{account['id']}", headers=headers)

    created = client.post(
        f"{API_BASE}/managed-agents",
        headers=headers,
        json={
            "name": AGENT_NAME,
            "source_kind": "high_code",
            "protocol": "openai_compatible",
            "endpoint": f"http://127.0.0.1:{st.mock_port}/v1",
            "model_name": "mock",
            "health_method": "test_invoke",
            "credential": {"auth_type": "none"},
            "agent_card": {"inputs": [{"key": "query", "type": "string", "required": True}]},
        },
    ).json()
    if created.get("status_code") == 12111:
        pytest.skip("allow_private_networks/egress_allowlist is not enabled for the local mock")
    assert created.get("status_code") == 200, created
    st.agent_id = created["data"]["id"]

    published = client.post(f"{API_BASE}/managed-agents/{st.agent_id}/publish", headers=headers).json()
    assert published.get("status_code") == 200, published

    account = client.post(
        f"{API_BASE}/service-accounts",
        headers=headers,
        json={"name": ACCOUNT_NAME, "description": "e2e managed agents"},
    ).json()
    assert account.get("status_code") == 200, account
    st.account_id = account["data"]["id"]

    grant = client.post(
        f"{API_BASE}/service-accounts/{st.account_id}/resource-grants:mutate",
        headers=headers,
        json={
            "grants": [{"resource_type": "managed_agent", "resource_id": str(st.agent_id), "level": "editor"}],
            "revokes": [],
        },
    )
    assert grant.status_code == 200, grant.text

    key = client.post(
        f"{API_BASE}/service-accounts/{st.account_id}/keys",
        headers=headers,
        json={"name": "e2e-managed", "scopes": ["managed_agent:invoke", "schedule:trigger"]},
    ).json()
    assert key.get("status_code") == 200, key
    st.key = key["data"]["plaintext"]

    yield st

    if st.task_id:
        client.delete(f"{API_BASE}/scheduled-tasks/{st.task_id}", headers=headers)
    if st.agent_id:
        client.delete(f"{API_BASE}/managed-agents/{st.agent_id}", headers=headers)
    if st.taxonomy_id:
        client.delete(f"{API_BASE}/managed-agent-taxonomies/{st.taxonomy_id}", headers=headers)
    if st.account_id:
        client.delete(f"{API_BASE}/service-accounts/{st.account_id}", headers=headers)


def test_agent_list_and_detail_never_leak_credentials(client, state):
    headers = _bearer(state.token)
    listed = client.get(f"{API_BASE}/managed-agents", headers=headers, params={"keyword": AGENT_NAME}).json()
    row = next(item for item in listed["data"]["data"] if item["id"] == state.agent_id)
    assert row["credential_type"] == "none"
    assert "credential_ciphertext" not in row


def test_unified_invoke_requires_key(client, state):
    resp = client.post(f"{V2_BASE}/managed-agent/invoke", json={"agent_id": state.agent_id, "input": {"query": "hi"}})
    assert resp.status_code == 401
    assert resp.json()["status_code"] == 11801


def test_unified_invoke_rejects_extra_fields(client, state):
    resp = client.post(
        f"{V2_BASE}/managed-agent/invoke",
        headers={"Authorization": f"Bearer {state.key}"},
        json={"agent_id": state.agent_id, "input": {"query": "hi"}, "unexpected": True},
    )
    assert resp.status_code == 400
    assert resp.json()["status_code"] == 12114

    # A bare identity is caught by F076's dependency before the endpoint's
    # strict whitelist, so the observable code is 11817 (documented in invoke.py).
    resp = client.post(
        f"{V2_BASE}/managed-agent/invoke",
        headers={"Authorization": f"Bearer {state.key}"},
        json={"agent_id": state.agent_id, "input": {"query": "hi"}, "user_id": 1},
    )
    assert resp.status_code == 400
    assert resp.json()["status_code"] == 11817


def test_unified_invoke_success(client, state):
    resp = client.post(
        f"{V2_BASE}/managed-agent/invoke",
        headers={"Authorization": f"Bearer {state.key}"},
        json={"agent_id": state.agent_id, "input": {"query": "hi"}},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["data"]["output"] == "pong"
    assert body["data"]["run_id"]


def test_classification_and_catalog(client, state):
    headers = _bearer(state.token)
    taxonomy = client.post(
        f"{API_BASE}/managed-agent-taxonomies",
        headers=headers,
        json={"kind": "scenario", "code": f"e2e-{SUFFIX}", "name_zh": "E2E 场景"},
    ).json()
    assert taxonomy.get("status_code") == 200, taxonomy
    state.taxonomy_id = taxonomy["data"]["id"]

    updated = client.put(
        f"{API_BASE}/managed-agents/{state.agent_id}",
        headers=headers,
        json={"scenario_code": f"e2e-{SUFFIX}"},
    ).json()
    assert updated.get("status_code") == 200, updated

    catalog = client.get(
        f"{API_BASE}/managed-agents/catalog", headers=headers, params={"scenario_code": f"e2e-{SUFFIX}"}
    ).json()
    assert any(item["id"] == state.agent_id for item in catalog["data"]["data"])


def test_scheduled_task_can_target_managed_agent(client, state):
    headers = _bearer(state.token)
    task = client.post(
        f"{API_BASE}/scheduled-tasks",
        headers=headers,
        json={
            "name": f"{PREFIX}task-{SUFFIX}",
            "app_id": str(state.agent_id),
            "app_type": 100,
            "schedule_type": "cron",
            "cron_expression": "*/5 * * * *",
            "service_account_id": state.account_id,
            "preset_inputs": [{"key": "query", "value": "hi"}],
        },
    ).json()
    assert task.get("status_code") == 200, task
    state.task_id = task["data"]["id"]

    run = client.post(f"{API_BASE}/scheduled-tasks/{state.task_id}/run", headers=headers).json()
    assert run.get("status_code") in (200, 11909, 11910), run


def test_custom_http_adapter(client, state):
    headers = _bearer(state.token)
    created = client.post(
        f"{API_BASE}/managed-agents",
        headers=headers,
        json={
            "name": f"{PREFIX}custom-{SUFFIX}",
            "source_kind": "high_code",
            "protocol": "custom_http",
            "endpoint": f"http://127.0.0.1:{state.mock_port}/custom",
            "health_method": "test_invoke",
            "credential": {"auth_type": "none"},
            "agent_card": {"inputs": [{"key": "query", "type": "string", "required": True}]},
        },
    ).json()
    assert created.get("status_code") == 200, created
    agent_id = created["data"]["id"]
    try:
        assert client.post(f"{API_BASE}/managed-agents/{agent_id}/publish", headers=headers).json()["status_code"] == 200
        grant = client.post(
            f"{API_BASE}/service-accounts/{state.account_id}/resource-grants:mutate",
            headers=headers,
            json={"grants": [{"resource_type": "managed_agent", "resource_id": str(agent_id), "level": "editor"}], "revokes": []},
        )
        assert grant.status_code == 200, grant.text
        invoked = client.post(
            f"{V2_BASE}/managed-agent/invoke",
            headers={"Authorization": f"Bearer {state.key}"},
            json={"agent_id": agent_id, "input": {"query": "hi"}},
        )
        assert invoked.status_code == 200, invoked.text
        assert invoked.json()["data"]["output"] == "custom-pong"
    finally:
        client.delete(f"{API_BASE}/managed-agents/{agent_id}", headers=headers)


def test_health_check_and_event_trigger(client, state):
    headers = _bearer(state.token)
    health = client.post(f"{API_BASE}/managed-agents/{state.agent_id}/health-check", headers=headers).json()
    assert health.get("status_code") == 200, health
    assert health["data"]["health_status"] == "healthy"

    event = client.post(
        f"{V2_BASE}/schedule/trigger",
        headers={
            "Authorization": f"Bearer {state.key}",
            "X-Bs-Timestamp": str(int(time.time())),
            "X-Bs-Nonce": uuid.uuid4().hex,
        },
        json={"task_id": state.task_id},
    )
    body = event.json()
    assert event.status_code == 200, body
    assert body.get("status_code") in (200, 11910), body


def test_delete_is_blocked_while_task_enabled(client, state):
    headers = _bearer(state.token)
    resp = client.delete(f"{API_BASE}/managed-agents/{state.agent_id}", headers=headers).json()
    assert resp.get("status_code") == 12110, resp
