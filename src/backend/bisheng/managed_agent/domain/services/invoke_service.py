"""F078 unified invocation orchestration.

Order is deliberate: tenant-scoped row resolution → state → permission →
rate limit → adapter. A missing/other-tenant/unauthorized agent is 404/11809
(anti-enumeration); an offline/disabled/draft agent is 12105 only after the
caller has shown it can see the row. Every attempt writes a
``managed_agent_run`` row (success or failure) and a best-effort DB-only
``managed_agent.invoke`` audit row; input bodies and credentials are never
stored.
"""

from __future__ import annotations

import time
import uuid

from loguru import logger

from bisheng.common.errcode.base import BaseErrorCode
from bisheng.common.errcode.managed_agent import (
    ManagedAgentUnavailableError,
    ManagedAgentUpstreamError,
)
from bisheng.common.errcode.open_api import OpenApiNotFoundError
from bisheng.database.models.audit_log import AuditLogDao
from bisheng.managed_agent.domain.constants import ManagedAgentAuditAction
from bisheng.managed_agent.domain.models.dao import ManagedAgentDao, ManagedAgentRunDao
from bisheng.managed_agent.domain.models.managed_agent import AgentStatus, ManagedAgent
from bisheng.managed_agent.domain.models.run import (
    CallerType,
    ManagedAgentRun,
    ManagedRunStatus,
)
from bisheng.managed_agent.domain.services.adapter_registry import get_adapter
from bisheng.managed_agent.domain.services.adapters.base import (
    AgentResult,
    CallerIdentity,
)
from bisheng.managed_agent.domain.services.rate_limiter import consume as consume_rate
from bisheng.permission.domain.services.permission_service import PermissionService


async def invoke(
    user,
    agent_id: int,
    payload,
    *,
    session_id: str | None = None,
    caller_type: str = CallerType.API_KEY.value,
    caller_id: str | None = None,
    service_account_id: int | None = None,
    scheduled_task_id: int | None = None,
    trigger_kind: str | None = None,
) -> dict:
    agent = await ManagedAgentDao.aget(agent_id)
    if agent is None:
        raise OpenApiNotFoundError()
    allowed = await PermissionService.check(
        user.user_id,
        "editor",
        "managed_agent",
        str(agent_id),
        login_user=user,
    )
    if not allowed:
        raise OpenApiNotFoundError()
    if agent.status != AgentStatus.ONLINE.value:
        raise ManagedAgentUnavailableError(msg=f"managed agent is {agent.status}")

    await consume_rate(agent.tenant_id, agent.id)
    result = await _execute(
        agent,
        payload,
        session_id=session_id,
        caller=CallerIdentity(
            user_id=user.user_id,
            tenant_id=agent.tenant_id,
            service_account_id=service_account_id,
        ),
        caller_type=caller_type,
        caller_id=caller_id,
        service_account_id=service_account_id,
        scheduled_task_id=scheduled_task_id,
        trigger_kind=trigger_kind,
    )
    return {
        "run_id": result.run_id,
        "output": result.output,
        "usage": result.usage,
    }


async def _execute(
    agent: ManagedAgent,
    payload,
    *,
    session_id: str | None,
    caller: CallerIdentity,
    caller_type: str,
    caller_id: str | None,
    service_account_id: int | None,
    scheduled_task_id: int | None,
    trigger_kind: str | None,
) -> AgentResult:
    started = time.monotonic()
    status = ManagedRunStatus.FAILED
    error_code: int | None = None
    error_summary: str | None = None
    upstream_status: int | None = None
    result: AgentResult | None = None
    try:
        adapter = get_adapter(agent)
        result = await adapter.invoke(agent, payload, session_id, caller)
        status = ManagedRunStatus.SUCCESS
        upstream_status = result.upstream_status
        return result
    except BaseErrorCode as exc:
        error_code = exc.code
        error_summary = str(exc)[:480]
        raise
    except Exception as exc:
        logger.exception("managed_agent: invoke failed for {}", agent.id)
        error_code = 12106
        error_summary = str(exc)[:480] or exc.__class__.__name__
        raise ManagedAgentUpstreamError(msg="managed agent invoke failed") from exc
    finally:
        latency_ms = int((time.monotonic() - started) * 1000)
        run_id = result.run_id if result else uuid.uuid4().hex
        trace_id = result.run_id if result else None
        await _record_run(
            agent,
            run_id=run_id,
            trace_id=trace_id,
            caller_type=caller_type,
            caller_id=caller_id,
            service_account_id=service_account_id,
            scheduled_task_id=scheduled_task_id,
            trigger_kind=trigger_kind,
            status=status.value,
            latency_ms=latency_ms,
            error_code=error_code,
            error_summary=error_summary,
            upstream_status=upstream_status,
            usage=result.usage if result else None,
        )
        await ManagedAgentDao.aupdate_last_call(
            agent.id,
            tenant_id=agent.tenant_id,
            status=status.value,
            latency_ms=latency_ms,
        )
        await _audit_invoke(agent, status=status.value, latency_ms=latency_ms, error_code=error_code, trace_id=trace_id)


async def _record_run(
    agent: ManagedAgent,
    *,
    run_id: str,
    trace_id: str | None,
    caller_type: str,
    caller_id: str | None,
    service_account_id: int | None,
    scheduled_task_id: int | None,
    trigger_kind: str | None,
    status: str,
    latency_ms: int,
    error_code: int | None,
    error_summary: str | None,
    upstream_status: int | None,
    usage: dict | None,
) -> None:
    try:
        await ManagedAgentRunDao.acreate(
            ManagedAgentRun(
                tenant_id=agent.tenant_id,
                agent_id=agent.id,
                agent_name_snapshot=agent.name,
                source_kind_snapshot=agent.source_kind,
                protocol_snapshot=agent.protocol,
                run_id=run_id,
                trace_id=trace_id,
                caller_type=caller_type,
                caller_id=caller_id,
                service_account_id=service_account_id,
                scheduled_task_id=scheduled_task_id,
                trigger_kind=trigger_kind,
                status=status,
                error_code=error_code,
                error_summary=error_summary,
                latency_ms=latency_ms,
                upstream_status=upstream_status,
                usage=usage,
            )
        )
    except Exception:
        logger.exception("managed_agent: run trace write failed for agent {}", agent.id)


async def _audit_invoke(
    agent: ManagedAgent,
    *,
    status: str,
    latency_ms: int,
    error_code: int | None,
    trace_id: str | None,
) -> None:
    try:
        await AuditLogDao.ainsert_v2(
            tenant_id=agent.tenant_id,
            operator_id=0,
            operator_tenant_id=agent.tenant_id,
            action=ManagedAgentAuditAction.INVOKE.value,
            target_type="managed_agent",
            target_id=str(agent.id),
            reason=status,
            metadata={
                "status": status,
                "latency_ms": latency_ms,
                "error_code": error_code,
                "trace_id": trace_id,
            },
            object_name=agent.name,
        )
    except Exception:
        logger.exception("managed_agent: invoke audit write failed for agent {}", agent.id)
