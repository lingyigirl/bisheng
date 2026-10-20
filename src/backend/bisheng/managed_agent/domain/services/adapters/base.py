"""F078 adapter contract shared by native and third-party agents."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol


@dataclass(frozen=True)
class CallerIdentity:
    """Who is invoking; the adapter never receives platform credentials."""

    user_id: int
    tenant_id: int
    service_account_id: int | None = None


@dataclass(frozen=True)
class AgentResult:
    run_id: str
    output: Any
    usage: dict | None = None
    upstream_status: int | None = None


@dataclass(frozen=True)
class HealthResult:
    ok: bool
    latency_ms: int
    detail: str | None = None


class ManagedAgentAdapter(Protocol):
    async def invoke(
        self,
        agent,
        payload: Any,
        session_id: str | None,
        caller: CallerIdentity,
    ) -> AgentResult: ...

    async def health_check(self, agent) -> HealthResult: ...
