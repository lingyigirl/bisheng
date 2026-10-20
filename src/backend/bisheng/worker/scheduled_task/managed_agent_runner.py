"""F078 managed-agent scheduled runner (Celery, default queue).

Used for third-party agents and native assistant projections. Native workflow
projections are dispatched through F077's ``_dispatch_workflow`` instead, so
this runner never executes a workflow.

It reuses ``invoke_service._execute`` (adapter + ``managed_agent_run`` trace +
last-call update) and then finalizes the scheduled task with the same
``_finalize`` used by the assistant runner. Failure counting is limited to
scheduled rounds; manual/event runs do not increment the consecutive counter
(F077 AC-17).
"""

from __future__ import annotations

from loguru import logger

from bisheng.common.errcode.base import BaseErrorCode
from bisheng.managed_agent.domain.models.dao import ManagedAgentDao
from bisheng.managed_agent.domain.services import invoke_service
from bisheng.managed_agent.domain.services.adapters.base import CallerIdentity
from bisheng.scheduled_task.domain.models.dao import ScheduledTaskDao
from bisheng.scheduled_task.domain.models.scheduled_task import RunStatus
from bisheng.scheduled_task.domain.services import run_service
from bisheng.worker._asyncio_utils import run_async_task
from bisheng.worker.main import bisheng_celery

_SCHEDULED_KINDS = {"scheduled", "cron", "once"}


@bisheng_celery.task(
    acks_late=True,
    time_limit=1800,
    soft_time_limit=1740,
    name="bisheng.worker.scheduled_task.managed_agent_runner.execute_managed_agent_run",
)
def execute_managed_agent_run(task_id: int, trace_id: str, trigger_kind: str = "scheduled") -> bool:
    return run_async_task(lambda: _execute(task_id, trace_id, trigger_kind))


async def _execute(task_id: int, trace_id: str, trigger_kind: str) -> bool:
    task = await ScheduledTaskDao.aget(task_id)
    if task is None:
        logger.warning("managed_agent: scheduled task {} disappeared before run", task_id)
        return False

    started_at = run_service._now()
    status = RunStatus.FAILED
    error: str | None = None
    try:
        agent = await ManagedAgentDao.aget(int(task.app_id))
        if agent is None:
            error = "managed_agent_deleted"
            return False
        payload = {item.get("key"): item.get("value") for item in (task.preset_inputs or []) if item.get("key")}
        await invoke_service._execute(
            agent,
            payload,
            session_id=None,
            caller=CallerIdentity(
                user_id=task.run_identity_user_id,
                tenant_id=task.tenant_id,
                service_account_id=task.service_account_id,
            ),
            caller_type="schedule",
            caller_id=str(task.id),
            service_account_id=task.service_account_id,
            scheduled_task_id=task.id,
            trigger_kind=trigger_kind,
        )
        status = RunStatus.SUCCESS
        return True
    except BaseErrorCode as exc:
        error = str(exc)[:480] or exc.__class__.__name__
        return False
    except Exception as exc:
        logger.exception("managed_agent: scheduled run failed for task {}", task_id)
        error = str(exc)[:480] or exc.__class__.__name__
        return False
    finally:
        await run_service._finalize(
            task,
            token=trace_id,
            status=status,
            error=error,
            now=run_service._now(),
            notify=run_service._notify,
            count_failure=(status is RunStatus.FAILED and trigger_kind in _SCHEDULED_KINDS),
            started_at=started_at,
            trace_id=trace_id,
        )
