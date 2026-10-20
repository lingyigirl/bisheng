"""F078 managed-agent health scan (beat entry ``managed_agent_health_scan``).

The beat tick is fixed at 300s; per-agent policy lives in ``ManagedAgentConf``.
The loop is idempotent (it only reads health and writes status fields), so
``acks_late`` is safe; one broken row never stops the round.
"""

from __future__ import annotations

from loguru import logger

from bisheng.managed_agent.domain.services import health_service
from bisheng.worker._asyncio_utils import run_async_task
from bisheng.worker.main import bisheng_celery


@bisheng_celery.task(
    acks_late=True,
    time_limit=300,
    soft_time_limit=270,
    name="bisheng.worker.managed_agent.health_scan.scan_managed_agents",
)
def scan_managed_agents() -> dict:
    try:
        return run_async_task(health_service.scan_once)
    except Exception:
        logger.exception("managed_agent: health scan round crashed")
        return {"crashed": True}
