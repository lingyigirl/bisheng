from fastapi import APIRouter

from .endpoints import agent_transactions, call_logs, dashboard

router = APIRouter(prefix="/telemetry", tags=["TelemetrySearch"])

router.include_router(dashboard.router)
router.include_router(call_logs.router)
router.include_router(agent_transactions.router)

__all__ = ["router"]
