"""Shared outbound HTTP helpers for third-party adapters.

httpx defaults are unsafe for this feature: ``trust_env=True`` (environment
proxies), a 5s per-phase timeout and no body cap. Every client below sets the
timeouts/limits explicitly, disables redirects and env proxies, and streams the
response under ``max_response_bytes``.
"""

from __future__ import annotations

import json
from typing import Any

import httpx

from bisheng.common.errcode.managed_agent import (
    ManagedAgentCredentialInvalidError,
    ManagedAgentUpstreamResponseInvalidError,
)
from bisheng.common.services.config_service import settings
from bisheng.managed_agent.domain.services.credential_service import decrypt_credential


def _conf():
    return settings.managed_agent


def build_async_client() -> httpx.AsyncClient:
    conf = _conf()
    kwargs: dict[str, Any] = {
        "timeout": httpx.Timeout(
            connect=conf.connect_timeout_seconds,
            read=conf.read_timeout_seconds,
            write=conf.write_timeout_seconds,
            pool=conf.pool_timeout_seconds,
        ),
        "follow_redirects": False,
        "verify": True,
        "trust_env": False,
        "limits": httpx.Limits(max_connections=20, max_keepalive_connections=10),
    }
    if conf.proxy:
        kwargs["proxy"] = conf.proxy
    return httpx.AsyncClient(**kwargs)


def credential_headers(agent) -> dict[str, str]:
    payload = decrypt_credential(agent.credential_ciphertext)
    headers = dict(payload.headers)
    if payload.auth_type == "bearer" and payload.secret:
        headers["Authorization"] = f"Bearer {payload.secret}"
    elif payload.auth_type == "api_key" and payload.secret:
        headers["X-API-Key"] = payload.secret
    return headers


async def read_limited(response: httpx.Response) -> bytes:
    cap = _conf().max_response_bytes
    chunks: list[bytes] = []
    total = 0
    async for chunk in response.aiter_bytes():
        total += len(chunk)
        if total > cap:
            raise ManagedAgentUpstreamResponseInvalidError(msg=f"response exceeds {cap} bytes")
        chunks.append(chunk)
    return b"".join(chunks)


def ensure_credential(agent) -> None:
    if agent.has_credential and not agent.credential_ciphertext:
        raise ManagedAgentCredentialInvalidError(msg="credential is marked present but missing")


def json_or_raise(response: httpx.Response, body: bytes) -> dict:
    try:
        data = json.loads(body) if body else {}
    except Exception as exc:
        raise ManagedAgentUpstreamResponseInvalidError(msg="upstream response is not JSON") from exc
    if not isinstance(data, dict):
        raise ManagedAgentUpstreamResponseInvalidError(msg="upstream response is not an object")
    return data
