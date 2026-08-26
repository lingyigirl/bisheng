"""Unit tests for MiniMaxTTSClient (F045 MiniMax TTS adapter).

Covers the MiniMax-native T2A protocol spoken to the aiping gateway
(``/audio/speech``, base64 response) and the MiniMax official API
(``/t2a_v2``, hex response) without any network or DB access — the
client under test is fed a pre-built ``httpx.AsyncClient`` whose
transport is a ``MockTransport``.

AC coverage: AC-02, AC-03, AC-04, AC-05, AC-06, AC-07, AC-10
(registration mapping: AC-01, AC-09).
"""
import ast
import base64
import os
import unittest
from unittest.mock import AsyncMock

import httpx
import pytest

from bisheng.core.ai.tts.minimax_tts_client import MiniMaxTTSClient

_TTS_PY_PATH = os.path.normpath(os.path.join(
    os.path.dirname(__file__), '..', '..', 'bisheng', 'llm', 'domain', 'llm', 'tts.py'))

FAKE_MP3 = b'\xff\xfb\x90\x04fake-mp3-bytes'
API_KEY = 'test-key'
DEFAULT_BASE = 'https://aiping.cn/api/v1'


def _client_with_handler(handler, **kwargs) -> MiniMaxTTSClient:
    """Build a client whose HTTP layer is the given MockTransport handler.

    A default voice is configured so response-path tests don't trip the
    fail-fast voice check; pass ``voice=None`` to exercise that path.
    """
    transport = httpx.MockTransport(handler)
    http_client = httpx.AsyncClient(transport=transport)
    kwargs.setdefault('base_url', DEFAULT_BASE)
    kwargs.setdefault('voice', 'default-voice')
    return MiniMaxTTSClient(api_key=API_KEY, http_client=http_client, **kwargs)


def _base64_response() -> dict:
    return {'data': {'audio': base64.b64encode(FAKE_MP3).decode()}}


def _hex_response() -> dict:
    return {'data': {'audio': FAKE_MP3.hex()}}


@pytest.mark.asyncio
async def test_request_payload_shape_and_auth():
    """AC-02: request body is MiniMax-native (text/voice_setting/audio_setting)."""
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured['url'] = str(request.url)
        captured['method'] = request.method
        captured['json'] = _parse_json(request)
        captured['auth'] = request.headers.get('authorization')
        captured['content_type'] = request.headers.get('content-type')
        return httpx.Response(200, json=_base64_response())

    client = _client_with_handler(handler, model='MiniMax-Speech-2.8-hd', voice='male-qn-qingse')
    result = await client.synthesize('你好')

    assert captured['method'] == 'POST'
    assert captured['url'] == f'{DEFAULT_BASE}/audio/speech'
    assert captured['auth'] == f'Bearer {API_KEY}'
    assert captured['content_type'] == 'application/json'
    body = captured['json']
    assert body['model'] == 'MiniMax-Speech-2.8-hd'
    assert body['text'] == '你好'
    assert body['voice_setting'] == {'voice_id': 'male-qn-qingse', 'speed': 1.0, 'vol': 1.0}
    assert body['audio_setting'] == {'sample_rate': 32000, 'format': 'mp3', 'channel': 1}
    assert 'input' not in body
    assert result == FAKE_MP3


@pytest.mark.asyncio
async def test_base64_audio_decoded():
    """AC-03: aiping gateway base64 response decoded to audio bytes."""
    client = _client_with_handler(lambda req: httpx.Response(200, json=_base64_response()))
    assert await client.synthesize('hello') == FAKE_MP3


@pytest.mark.asyncio
async def test_hex_audio_decoded():
    """AC-04: MiniMax official hex response decoded to audio bytes."""
    client = _client_with_handler(lambda req: httpx.Response(200, json=_hex_response()))
    assert await client.synthesize('hello') == FAKE_MP3


@pytest.mark.asyncio
async def test_hex_looking_audio_not_misdecoded_as_base64():
    """A hex-only payload decodes via fromhex, never lenient base64."""
    # '494433' is valid base64 alphabet but must decode as hex 'ID3'
    client = _client_with_handler(
        lambda req: httpx.Response(200, json={'data': {'audio': '494433'}}))
    assert await client.synthesize('hello') == b'ID3'


@pytest.mark.asyncio
async def test_non_200_raises_with_provider_info():
    """AC-05: non-200 raises an exception carrying status + response body."""
    client = _client_with_handler(
        lambda req: httpx.Response(400, json={'detail': {'message': 'business error'}}))
    with pytest.raises(Exception, match='400'):
        await client.synthesize('hello')
    with pytest.raises(Exception, match='business error'):
        await client.synthesize('hello')


@pytest.mark.asyncio
async def test_missing_audio_field_raises():
    """AC-05: response without data.audio raises instead of returning garbage."""
    client = _client_with_handler(lambda req: httpx.Response(200, json={'data': {}}))
    with pytest.raises(Exception, match=r'data\.audio'):
        await client.synthesize('hello')


@pytest.mark.asyncio
async def test_invalid_json_response_raises():
    """AC-05: non-JSON 200 body raises."""
    client = _client_with_handler(
        lambda req: httpx.Response(200, content=b'not json'))
    with pytest.raises(Exception, match=r'valid JSON'):
        await client.synthesize('hello')


@pytest.mark.asyncio
async def test_undecodable_audio_raises():
    """AC-05: audio that is neither valid hex nor strict base64 raises."""
    client = _client_with_handler(
        lambda req: httpx.Response(200, json={'data': {'audio': '@@@not-hex-and-invalid-b64@@@'}}))
    with pytest.raises(Exception, match='decode'):
        await client.synthesize('hello')


@pytest.mark.asyncio
async def test_voice_parameter_overrides_config():
    """AC-06: synthesize(voice=...) wins over the client's configured voice."""
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured['voice_id'] = _parse_json(request)['voice_setting']['voice_id']
        return httpx.Response(200, json=_base64_response())

    client = _client_with_handler(handler, voice='config-voice')
    await client.synthesize('hello', voice='caller-voice')
    assert captured['voice_id'] == 'caller-voice'

    await client.synthesize('hello')
    assert captured['voice_id'] == 'config-voice'


@pytest.mark.asyncio
async def test_missing_voice_fails_fast_without_http():
    """AC-06: no configured voice and no override → clear error, no request."""
    handler = AsyncMock(return_value=httpx.Response(200, json=_base64_response()))
    client = _client_with_handler(handler, voice=None)  # no voice configured
    with pytest.raises(Exception, match='voice'):
        await client.synthesize('hello')
    handler.assert_not_awaited()


@pytest.mark.asyncio
async def test_endpoint_override():
    """AC-07: endpoint config overrides the default /audio/speech path."""
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured['url'] = str(request.url)
        return httpx.Response(200, json=_base64_response())

    client = _client_with_handler(handler, endpoint='/t2a_v2')
    await client.synthesize('hello')
    assert captured['url'] == f'{DEFAULT_BASE}/t2a_v2'


@pytest.mark.asyncio
async def test_default_endpoint_is_aiping_gateway():
    """AC-07: default endpoint is /audio/speech (aiping gateway)."""
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured['url'] = str(request.url)
        return httpx.Response(200, json=_base64_response())

    client = _client_with_handler(handler)
    await client.synthesize('hello')
    assert captured['url'] == f'{DEFAULT_BASE}/audio/speech'


def _parse_json(request: httpx.Request) -> dict:
    import json
    return json.loads(request.content.decode())


class TestMinimaxTTSRegistration(unittest.TestCase):
    """AC-01/AC-09: minimax type maps to MiniMaxTTSClient; the other three
    client types stay untouched. AST inspection mirrors test_minimax_provider.py
    so the heavy llm/domain module is never imported (no DB dependency)."""

    @classmethod
    def setUpClass(cls):
        with open(_TTS_PY_PATH) as f:
            cls.source = f.read()
        cls.tree = ast.parse(cls.source)

    def _get_dict_source(self, name: str) -> str:
        for node in ast.walk(self.tree):
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name) and target.id == name:
                        return ast.get_source_segment(self.source, node)
        return ''

    def test_minimax_registered_with_dedicated_client(self):
        """_tts_client_type['minimax'] uses MiniMaxTTSClient."""
        entry = self._get_dict_source('_tts_client_type')
        assert 'MINIMAX.value' in entry
        assert '"client": MiniMaxTTSClient' in entry

    def test_get_minimax_params_builds_expected_kwargs(self):
        """_get_minimax_params threads api_key/model/base_url/endpoint/voice."""
        for node in ast.walk(self.tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == '_get_minimax_params':
                src = ast.get_source_segment(self.source, node)
                assert 'openai_api_key' in src
                assert 'model_info.model_name' in src
                assert 'endpoint' in src
                assert 'voice' in src
                assert 'openai_proxy' in src
                return
        raise AssertionError('_get_minimax_params should exist in tts.py')

    def test_existing_client_types_untouched(self):
        """openai / azure_openai / qwen mappings keep their original clients."""
        entry = self._get_dict_source('_tts_client_type')
        assert '"client": OpenAITTSClient' in entry
        assert '"client": AzureOpenAITTSClient' in entry
        assert '"client": AliyunTTSClient' in entry
