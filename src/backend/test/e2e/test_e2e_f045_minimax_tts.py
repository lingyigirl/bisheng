"""E2E tests for F045 MiniMax TTS adapter.

The external TTS provider is stubbed by an in-process HTTP server speaking
the MiniMax-native T2A protocol (aiping gateway base64 variant + official
hex variant), so the whole platform chain — ``POST /llm`` CRUD → creation-time
synthesis → ``BishengTTS`` factory → ``MiniMaxTTSClient`` → HTTP — is
exercised offline and repeatably.

Prerequisites: backend on localhost:7860, middleware running.

Covers:
- AC-01: minimax provider creates a tts model; creation-time real synthesis
  passes; model status normal (0) and online
- AC-02: wire format spoken to the provider is MiniMax-native
  (text + voice_setting.voice_id + Bearer auth; no OpenAI ``input``)
- AC-09: generic LLM CRUD auth surface unchanged — a plain user is still
  rejected when creating a provider

AC-08 (UI dropdown) and AC-11 (workbench default TTS against the real aiping
account) are manual checklist items — see
features/v2.7.0/045-minimax-tts-adapter/e2e-checklist.md.
"""
import base64
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import httpx
import pytest

from test.e2e.helpers.api import API_BASE
from test.e2e.helpers.auth import auth_headers, create_test_user, get_admin_token, get_user_token

PREFIX = 'e2e-f045-'
TEST_USER_PASSWORD = 'Test@1234'  # env password policy: upper+lower+digit+symbol
FAKE_MP3 = b'ID3\x04\x00\x00\x00\x00\x00\x00' + b'f' * 200


class _MiniMaxStubHandler(BaseHTTPRequestHandler):
    """MiniMax-native T2A stub: /audio/speech returns base64, /t2a_v2 returns hex."""

    def do_POST(self):
        length = int(self.headers.get('content-length', 0))
        payload = json.loads(self.rfile.read(length) or b'{}')
        self.server.last_payload = payload
        self.server.last_auth = self.headers.get('authorization')
        if self.path == '/audio/speech':
            audio = base64.b64encode(FAKE_MP3).decode()
        elif self.path == '/t2a_v2':
            audio = FAKE_MP3.hex()
        else:
            self.send_response(404)
            self.end_headers()
            return
        body = json.dumps({'data': {'audio': audio}}).encode()
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):  # silence stub access logs
        pass


@pytest.fixture(scope='module')
def stub():
    server = HTTPServer(('127.0.0.1', 0), _MiniMaxStubHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield {'url': f'http://127.0.0.1:{server.server_port}', 'server': server}
    server.shutdown()


def _server_payload(name: str, base_url: str, voice: str = 'male-qn-qingse') -> dict:
    return {
        'name': f'{PREFIX}{name}',
        'description': 'e2e f045 stub provider',
        'type': 'minimax',
        'limit_flag': False,
        'limit': 0,
        'config': {
            'openai_api_base': base_url,
            'openai_api_key': 'e2e-stub-key',
        },
        'share_to_children': True,
        'models': [{
            'name': 'MiniMax-Speech-2.8-hd',
            'model_name': 'MiniMax-Speech-2.8-hd',
            'model_type': 'tts',
            'online': True,
            'config': {'voice': voice},
        }],
    }


async def _delete_llm_servers_by_prefix(client: httpx.AsyncClient, admin_token: str):
    """Feature-specific cleanup: DELETE /llm takes a JSON body."""
    headers = auth_headers(admin_token)
    resp = await client.get('/llm', headers=headers)
    servers = (resp.json().get('data') or []) if resp.status_code == 200 else []
    for s in servers:
        if (s.get('name') or '').startswith(PREFIX):
            await client.request('DELETE', '/llm', headers=headers, json={'server_id': s['id']})


class TestE2EF045MinimaxTTS:
    """E2E: F045 minimax TTS adapter via stubbed provider."""

    @pytest.fixture(autouse=True)
    async def setup_and_teardown(self, client):
        admin_token = await get_admin_token(client)
        # Setup: purge leftovers of previous runs (double cleanup)
        await _delete_llm_servers_by_prefix(client, admin_token)
        yield {'admin_token': admin_token}
        # Teardown: remove everything this suite created
        await _delete_llm_servers_by_prefix(client, admin_token)

    @pytest.fixture
    async def client(self):
        async with httpx.AsyncClient(base_url=API_BASE, timeout=60.0) as c:
            yield c

    async def test_ac01_create_minimax_tts_model_passes_connectivity(
            self, client, stub, setup_and_teardown):
        """AC-01: minimax provider + tts model creation passes the real
        creation-time synthesis test (stubbed provider) → status=0, online."""
        admin_token = setup_and_teardown['admin_token']
        headers = auth_headers(admin_token)
        resp = await client.post('/llm', headers=headers, json=_server_payload('tts', stub['url']))
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body['status_code'] == 200, body
        server_id = body['data']['id']
        assert body['data']['models'][0]['model_type'] == 'tts'

        # AC-01: GET reflects normal status and online
        resp = await client.get('/llm', headers=headers)
        listed = next(s for s in resp.json()['data'] if s['id'] == server_id)
        listed_model = next(m for m in listed['models'] if m['model_type'] == 'tts')
        assert listed_model['status'] == 0, listed_model.get('remark')
        assert listed_model['online'] is True

    async def test_ac02_wire_format_is_minimax_native(self, client, stub, setup_and_teardown):
        """AC-02: the creation-time synthesis spoke MiniMax-native protocol:
        text + voice_setting.voice_id + Bearer auth, no OpenAI ``input``."""
        admin_token = setup_and_teardown['admin_token']
        headers = auth_headers(admin_token)
        resp = await client.post(
            '/llm', headers=headers, json=_server_payload('wire', stub['url'], voice='female-shaonv'))
        assert resp.status_code == 200, resp.text

        payload = stub['server'].last_payload
        assert payload is not None, 'stub never received a synthesis request'
        assert payload['text'] == 'hello'  # test_model_status invokes "hello"
        assert 'input' not in payload
        assert payload['voice_setting']['voice_id'] == 'female-shaonv'
        assert payload['audio_setting']['format'] == 'mp3'
        assert stub['server'].last_auth == 'Bearer e2e-stub-key'

    async def test_ac09_non_admin_create_still_rejected(self, client, setup_and_teardown):
        """AC-09: generic LLM CRUD auth surface unchanged — plain user 401.

        Note: this platform version has no user-delete API (F012 removed
        tenant-membership deletion), so the created user stays — a unique
        per-run name keeps the test idempotent.
        """
        import uuid

        admin_token = setup_and_teardown['admin_token']
        username = f'e2e_f045_{uuid.uuid4().hex[:8]}'
        await create_test_user(client, admin_token, username, password=TEST_USER_PASSWORD)
        user_token = await get_user_token(client, username, TEST_USER_PASSWORD)
        resp = await client.post(
            '/llm', headers=auth_headers(user_token),
            json=_server_payload('forbidden', 'http://127.0.0.1:1'))
        # UnifiedResponseModel envelope: business rejection carries code 19801
        body = resp.json()
        assert body['status_code'] == 19801, resp.text
