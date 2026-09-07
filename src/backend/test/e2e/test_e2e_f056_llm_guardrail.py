"""F056 e2e: LLM guardrail real-chain verification (AC-01/02/03/06/07/08/15/17).

Real backend + middleware required (localhost:7860, override via E2E_API_BASE).

Coverage note (tasks.md T018): ws 拦截 (AC-05) 与真实流式输出 (AC-10/11/12/14)
依赖在线工作流 + 浏览器 ws 链路, 见 `features/v2.7.0/056-llm-guardrail/
e2e-checklist.md` 手动清单; AC-04 租户隔离由单测覆盖 (本部署单租户)。
"""

import os
import uuid

import httpx
import pytest

from test.e2e.helpers.api import API_BASE, assert_resp_200
from test.e2e.helpers.auth import auth_headers, encrypt_password

V2_BASE = API_BASE.replace('/api/v1', '/api/v2')
HIT_WORD = f'违禁词{uuid.uuid4().hex[:6]}'
AUTO_REPLY = 'E2E 拦截话术: 请修改输入'


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
    password = os.environ.get('E2E_ADMIN_PASSWORD', 'admin123')
    return _login(client, 'admin', password)


@pytest.fixture(scope="module")
def created_assistant_ids():
    ids: list[str] = []
    yield ids


@pytest.fixture(scope="module", autouse=True)
def _cleanup(client, admin_token, created_assistant_ids):
    """Teardown: restore policies to disabled and delete created assistants."""
    yield
    for business_type in ('chat_input', 'chat_output'):
        try:
            client.put(
                f'{API_BASE}/sensitive-word-policies/{business_type}',
                json={'enabled': False, 'words_types': [], 'custom_words': '', 'auto_reply': ''},
                headers=auth_headers(admin_token),
            )
        except Exception:  # noqa: BLE001 — best-effort teardown, never mask test results
            pass
    for aid in created_assistant_ids:
        try:
            client.post(
                f'{API_BASE}/assistant/delete',
                params={'assistant_id': aid},
                headers=auth_headers(admin_token),
            )
        except Exception:  # noqa: BLE001
            pass


def _put_policy(client, token, business_type, *, enabled, custom_words='', auto_reply='', extra=None):
    payload = {
        'enabled': enabled,
        'words_types': ['custom'] if custom_words else [],
        'custom_words': custom_words,
        'auto_reply': auto_reply,
    }
    if extra is not None:
        payload['extra_config'] = extra
    resp = client.put(
        f'{API_BASE}/sensitive-word-policies/{business_type}',
        json=payload,
        headers=auth_headers(token),
    )
    return assert_resp_200(resp)


def _create_assistant(client, token, ids) -> str:
    payload = {
        'name': f'f056-e2e-{uuid.uuid4().hex[:8]}',
        'prompt': 'F056 guardrail e2e round-trip assistant.',
        'logo': '',
    }
    data = assert_resp_200(client.post(f'{API_BASE}/assistant', json=payload, headers=auth_headers(token)))
    ids.append(str(data['id']))
    return str(data['id'])


def test_default_policies_disabled(client, admin_token):
    """AC-02/AC-17: 未配置时默认关闭, 对话链路零检测."""
    for business_type in ('chat_input', 'chat_output'):
        policy = assert_resp_200(
            client.get(f'{API_BASE}/sensitive-word-policies/{business_type}', headers=auth_headers(admin_token))
        )
        assert policy['enabled'] is False


def test_policy_roundtrip_and_immediate_effect(client, admin_token):
    """AC-01: 保存即生效; AC-03: 修改立即生效于后续请求 (无需重启)."""
    saved = _put_policy(
        client,
        admin_token,
        'chat_input',
        enabled=True,
        custom_words=HIT_WORD,
        auto_reply=AUTO_REPLY,
    )
    assert saved['enabled'] is True
    assert saved['custom_words'] == HIT_WORD
    assert saved['auto_reply'] == AUTO_REPLY

    fetched = assert_resp_200(
        client.get(f'{API_BASE}/sensitive-word-policies/chat_input', headers=auth_headers(admin_token))
    )
    assert fetched['enabled'] is True
    assert fetched['custom_words'] == HIT_WORD


def test_http_input_blocked_with_11201(client, admin_token, created_assistant_ids):
    """AC-08: OpenAI 兼容接口输入命中 → HTTP 400 + 错误码 11201 + 话术."""
    assistant_id = _create_assistant(client, admin_token, created_assistant_ids)
    resp = client.post(
        f'{V2_BASE}/assistant/chat/completions',
        json={
            'model': assistant_id,
            'messages': [{'role': 'user', 'content': f'请回答 {HIT_WORD} 是什么'}],
            'stream': False,
            'temperature': 0,
        },
    )
    assert resp.status_code == 400, f'HTTP {resp.status_code}: {resp.text[:200]}'
    body = resp.json()
    assert body['status_code'] == 11201
    assert body['status_message'] == AUTO_REPLY


def test_http_input_allowed_not_blocked(client, admin_token, created_assistant_ids):
    """AC-06: 未命中输入不触发拦截 (非 11201; 后续链路结果不在断言范围)."""
    assistant_id = _create_assistant(client, admin_token, created_assistant_ids)
    resp = client.post(
        f'{V2_BASE}/assistant/chat/completions',
        json={
            'model': assistant_id,
            'messages': [{'role': 'user', 'content': '今天天气怎么样'}],
            'stream': False,
            'temperature': 0,
        },
    )
    if resp.headers.get('content-type', '').startswith('application/json'):
        body = resp.json()
        assert body.get('status_code') != 11201, f'guard blocked a clean input: {body}'


def test_hit_log_recorded_without_raw_text(client, admin_token):
    """AC-07/AC-15: 命中日志落库, 仅摘要不存对话原文."""
    data = assert_resp_200(
        client.get(f'{API_BASE}/sensitive-word-hit-logs', headers=auth_headers(admin_token))
    )
    rows = data['data']
    assert data['total'] >= 1
    row = next((r for r in rows if r['direction'] == 'input' and any(h['word'] == HIT_WORD for h in r['hits'])), None)
    assert row is not None, f'no input hit log row for {HIT_WORD}'
    assert row['business_type'] == 'chat_input'
    assert row['action'] == 'block'
    # 不存对话原文: 行内不得出现任何文本内容字段
    assert all(key not in row for key in ('message', 'content', 'text', 'input'))


def test_output_policy_roundtrip(client, admin_token):
    """AC-01 (输出侧): 动作语义 round-trip."""
    saved = _put_policy(
        client,
        admin_token,
        'chat_output',
        enabled=True,
        custom_words=HIT_WORD,
        auto_reply=AUTO_REPLY,
        extra={'action': 'block'},
    )
    assert saved['extra_config']['action'] == 'block'
