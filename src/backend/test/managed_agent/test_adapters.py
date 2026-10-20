"""F078 T-15: OpenAI-compatible / custom HTTP adapter mapping and errors."""

from types import SimpleNamespace

import httpx
import pytest

from bisheng.common.errcode.managed_agent import (
    ManagedAgentOutboundRejectedError,
    ManagedAgentUpstreamError,
    ManagedAgentUpstreamResponseInvalidError,
)
from bisheng.managed_agent.domain.services.adapters import custom_http, openai_compatible
from bisheng.managed_agent.domain.services.adapters.base import CallerIdentity

CALLER = CallerIdentity(user_id=42, tenant_id=7)


def _agent(**overrides):
    base = {
        "endpoint": "https://agents.example.com/v1",
        "model_name": "crm-v1",
        "health_url": None,
        "credential_ciphertext": None,
        "has_credential": False,
        "credential_type": "none",
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def _client(handler):
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def test_openai_compatible_success(monkeypatch):
    async def _validated(url):
        return url

    def handler(request):
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": "hello"}}], "usage": {"total_tokens": 3}},
        )

    monkeypatch.setattr(openai_compatible, "_validated", _validated)
    monkeypatch.setattr(openai_compatible, "build_async_client", lambda: _client(handler))

    result = await openai_compatible.OpenAICompatibleAdapter().invoke(_agent(), {"query": "hi"}, None, CALLER)
    assert result.output == "hello"
    assert result.usage == {"total_tokens": 3}


async def test_custom_http_success(monkeypatch):
    async def _validated(url):
        return url

    def handler(request):
        return httpx.Response(200, json={"output": {"answer": 42}})

    monkeypatch.setattr(custom_http, "_validated", _validated)
    monkeypatch.setattr(custom_http, "build_async_client", lambda: _client(handler))

    result = await custom_http.CustomHttpAdapter().invoke(_agent(), {"query": "hi"}, "s-1", CALLER)
    assert result.output == {"answer": 42}


async def test_openai_compatible_4xx_maps_to_upstream_error(monkeypatch):
    async def _validated(url):
        return url

    monkeypatch.setattr(openai_compatible, "_validated", _validated)
    monkeypatch.setattr(
        openai_compatible,
        "build_async_client",
        lambda: _client(lambda request: httpx.Response(500, text="boom")),
    )

    with pytest.raises(ManagedAgentUpstreamError):
        await openai_compatible.OpenAICompatibleAdapter().invoke(_agent(), {"query": "hi"}, None, CALLER)


async def test_openai_compatible_redirect_rejected(monkeypatch):
    async def _validated(url):
        return url

    monkeypatch.setattr(openai_compatible, "_validated", _validated)
    monkeypatch.setattr(
        openai_compatible,
        "build_async_client",
        lambda: _client(lambda request: httpx.Response(302, headers={"Location": "https://evil.example.com"})),
    )

    with pytest.raises(ManagedAgentOutboundRejectedError):
        await openai_compatible.OpenAICompatibleAdapter().invoke(_agent(), {"query": "hi"}, None, CALLER)


async def test_invalid_json_maps_to_response_invalid(monkeypatch):
    async def _validated(url):
        return url

    monkeypatch.setattr(custom_http, "_validated", _validated)
    monkeypatch.setattr(
        custom_http,
        "build_async_client",
        lambda: _client(lambda request: httpx.Response(200, text="not-json")),
    )

    with pytest.raises(ManagedAgentUpstreamResponseInvalidError):
        await custom_http.CustomHttpAdapter().invoke(_agent(), {"query": "hi"}, None, CALLER)
