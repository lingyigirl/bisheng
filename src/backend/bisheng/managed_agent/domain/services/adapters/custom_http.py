"""Custom HTTP adapter: platform JSON contract, one request, no retry."""

from __future__ import annotations

import time
import uuid

import httpx

from bisheng.common.errcode.managed_agent import (
    ManagedAgentOutboundRejectedError,
    ManagedAgentUpstreamError,
    ManagedAgentUpstreamResponseInvalidError,
)
from bisheng.common.security.outbound_url import OutboundUrlRejected, avalidate_outbound_url
from bisheng.common.services.config_service import settings
from bisheng.managed_agent.domain.services.adapters.base import (
    AgentResult,
    CallerIdentity,
    HealthResult,
)
from bisheng.managed_agent.domain.services.adapters.http_utils import (
    build_async_client,
    credential_headers,
    ensure_credential,
    json_or_raise,
    read_limited,
)


def _conf():
    return settings.managed_agent


async def _validated(url: str) -> str:
    try:
        return await avalidate_outbound_url(
            url,
            allowlist=_conf().egress_allowlist,
            allow_private_networks=_conf().allow_private_networks,
        )
    except OutboundUrlRejected as exc:
        raise ManagedAgentOutboundRejectedError(msg=str(exc)) from exc


class CustomHttpAdapter:
    async def invoke(
        self,
        agent,
        payload,
        session_id: str | None,
        caller: CallerIdentity,
    ) -> AgentResult:
        ensure_credential(agent)
        url = await _validated(agent.endpoint or "")
        body = {
            "input": payload,
            "session_id": session_id,
            "caller": {"user_id": caller.user_id, "tenant_id": caller.tenant_id},
        }
        headers = {"Content-Type": "application/json", **credential_headers(agent)}
        try:
            async with build_async_client() as client:
                async with client.stream("POST", url, json=body, headers=headers) as response:
                    raw = await read_limited(response)
                    if 300 <= response.status_code < 400:
                        raise ManagedAgentOutboundRejectedError(msg="upstream redirect is not allowed")
                    if response.status_code >= 400:
                        raise ManagedAgentUpstreamError(
                            msg=f"upstream returned HTTP {response.status_code}",
                            upstream_status=response.status_code,
                        )
                    data = json_or_raise(response, raw)
        except (ManagedAgentOutboundRejectedError, ManagedAgentUpstreamError, ManagedAgentUpstreamResponseInvalidError):
            raise
        except httpx.TimeoutException as exc:
            raise ManagedAgentUpstreamError(msg="upstream timed out") from exc
        except httpx.HTTPError as exc:
            raise ManagedAgentUpstreamError(msg="upstream connection failed") from exc

        output = data.get("output", data.get("result", data.get("answer")))
        if output is None:
            raise ManagedAgentUpstreamResponseInvalidError(msg="upstream response has no output/result/answer")
        return AgentResult(
            run_id=uuid.uuid4().hex,
            output=output,
            usage=data.get("usage") if isinstance(data.get("usage"), dict) else None,
            upstream_status=response.status_code,
        )

    async def health_check(self, agent) -> HealthResult:
        if not agent.health_url:
            return HealthResult(ok=True, latency_ms=0, detail="health_url not configured")
        url = await _validated(agent.health_url)
        started = time.monotonic()
        try:
            async with build_async_client() as client:
                async with client.stream("GET", url, headers=credential_headers(agent)) as response:
                    await read_limited(response)
                    ok = 200 <= response.status_code < 300
                    return HealthResult(
                        ok=ok,
                        latency_ms=int((time.monotonic() - started) * 1000),
                        detail=f"HTTP {response.status_code}",
                    )
        except Exception as exc:
            return HealthResult(ok=False, latency_ms=int((time.monotonic() - started) * 1000), detail=str(exc)[:200])
