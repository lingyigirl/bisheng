"""F078 health checks: manual (audited) and periodic (beat, not audited).

A health check never changes business state on success; repeated failures mark
``health_status=unhealthy`` and, only when ``auto_offline_on_health_failure`` is
enabled, move the agent offline. The beat loop is strictly per-row tenant-scoped:
``bypass_tenant_filter`` for enumeration, then set/restore the tenant in a
``finally`` so a failure cannot leak the next row's scope.
"""

from __future__ import annotations

import time
from datetime import datetime
from types import SimpleNamespace

from loguru import logger

from bisheng.common.errcode.managed_agent import ManagedAgentHealthCheckFailedError
from bisheng.common.services.config_service import settings
from bisheng.core.context.tenant import (
    bypass_tenant_filter,
    get_current_tenant_id,
    set_current_tenant_id,
)
from bisheng.managed_agent.domain.constants import ManagedAgentAuditAction
from bisheng.managed_agent.domain.models.dao import ManagedAgentDao
from bisheng.managed_agent.domain.models.managed_agent import (
    AgentStatus,
    HealthMethod,
    HealthStatus,
)
from bisheng.managed_agent.domain.services.adapter_registry import get_adapter
from bisheng.managed_agent.domain.services.adapters.base import CallerIdentity
from bisheng.managed_agent.domain.services.agent_card import parse_agent_card


def _conf():
    return settings.managed_agent


def _sample_payload(agent) -> dict:
    card = parse_agent_card(agent.agent_card)
    payload = {}
    for item in card.inputs:
        if item.default is not None:
            payload[item.key] = item.default
    return payload


async def _check(agent) -> tuple[bool, int, str | None]:
    started = time.monotonic()
    try:
        adapter = get_adapter(agent)
        # Native projections: check the app's online state, never execute the
        # workflow/assistant on every beat (that would be a real run).
        if agent.source_kind == "native" or (agent.health_method == HealthMethod.HEALTH_URL.value and agent.health_url):
            result = await adapter.health_check(agent)
            return result.ok, result.latency_ms, result.detail
        await adapter.invoke(
            agent,
            _sample_payload(agent),
            None,
            CallerIdentity(user_id=agent.user_id, tenant_id=agent.tenant_id),
        )
        return True, int((time.monotonic() - started) * 1000), "test invoke"
    except Exception as exc:
        logger.warning("managed_agent: health check failed for {}: {}", agent.id, str(exc)[:200])
        return False, int((time.monotonic() - started) * 1000), str(exc)[:200]


async def _apply(agent, *, ok: bool, latency_ms: int, detail: str | None) -> None:
    now = datetime.now()
    failures = 0 if ok else int(agent.consecutive_health_failures or 0) + 1
    fields = {
        "health_status": (HealthStatus.HEALTHY if ok else HealthStatus.UNHEALTHY).value,
        "last_health_check_at": now,
        "last_health_ok_at": now if ok else agent.last_health_ok_at,
        "consecutive_health_failures": failures,
    }
    if not ok and _conf().auto_offline_on_health_failure and failures >= _conf().health_failure_threshold:
        fields["status"] = AgentStatus.OFFLINE.value
        logger.warning("managed_agent: auto-offline {} after {} health failures", agent.id, failures)
    await ManagedAgentDao.aupdate_health(agent.id, fields, tenant_id=agent.tenant_id)

    if not ok and failures == _conf().health_failure_threshold:
        try:
            from bisheng.scheduled_task.domain.services import run_service

            fake = SimpleNamespace(
                id=agent.id,
                name=agent.name,
                created_by=agent.user_id,
                tenant_id=agent.tenant_id,
            )
            await run_service._notify(
                fake,
                "managed_agent_health_failed",
                "Managed agent health check failed",
                f"{agent.name}: {detail or 'unknown'}",
            )
        except Exception:
            logger.exception("managed_agent: health notification failed for {}", agent.id)


async def run_health_check(user, agent_id: int):
    from bisheng.managed_agent.domain.services import registry_service

    agent = await registry_service.get_agent(user, agent_id)
    await registry_service._assert_manager_or_admin(user, agent_id)
    ok, latency_ms, detail = await _check(agent)
    await _apply(agent, ok=ok, latency_ms=latency_ms, detail=detail)
    updated = await ManagedAgentDao.aget(agent_id)
    if updated is None:
        raise ManagedAgentHealthCheckFailedError(msg="agent disappeared during health check")
    await registry_service._audit(
        user,
        ManagedAgentAuditAction.HEALTH_CHECK,
        updated,
        ok=ok,
        latency_ms=latency_ms,
        detail=detail,
    )
    if not ok:
        raise ManagedAgentHealthCheckFailedError(msg=detail or "health check failed")
    return updated


async def scan_once(*, limit: int = 500) -> dict:
    """One beat round: check online agents across tenants."""
    if not _conf().enabled:
        # The beat entry stays registered so enabling the feature does not need
        # a beat restart; the tick is simply a no-op while disabled.
        return {"checked": 0, "failed": 0, "total": 0}
    with bypass_tenant_filter():
        agents = await ManagedAgentDao.alist_health_scan(limit=limit)
    checked = 0
    failed = 0
    for agent in agents:
        previous = get_current_tenant_id()
        try:
            set_current_tenant_id(agent.tenant_id)
            ok, latency_ms, detail = await _check(agent)
            await _apply(agent, ok=ok, latency_ms=latency_ms, detail=detail)
            checked += 1
            failed += 0 if ok else 1
        except Exception:
            logger.exception("managed_agent: health scan failed for {}", agent.id)
        finally:
            set_current_tenant_id(previous)
    return {"checked": checked, "failed": failed, "total": len(agents)}
