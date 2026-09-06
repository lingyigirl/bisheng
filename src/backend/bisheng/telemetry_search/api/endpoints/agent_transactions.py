"""F052: agent-transaction monitor endpoints (transaction list + node detail).

Read-only queries for the /log 智能体监控 tab. Auth identical to the F046
call-logs endpoint (login-only); the log-menu permission check for non-admins
lives in the service (AC-18).
"""

from fastapi import APIRouter, Depends, Query, Request

from bisheng.api.v1.schemas import resp_200
from bisheng.common.dependencies.user_deps import UserPayload
from bisheng.telemetry_search.domain.schemas.agent_transactions import AgentTransactionQuery
from bisheng.telemetry_search.domain.services.agent_transactions import AgentTransactionsService

router = APIRouter(prefix="/agent-transactions", tags=["TelemetryAgentTransactions"])


@router.get("", summary="List agent-run transactions (application_process events)")
async def list_agent_transactions(
    request: Request,
    app_id: str = Query(default=None),
    app_type: str = Query(default=None),
    status: str = Query(default=None, description="success | failed"),
    keyword: str = Query(default=None, description="Search in the run input snapshot"),
    start_time: int = Query(default=None, description="Epoch seconds, inclusive"),
    end_time: int = Query(default=None, description="Epoch seconds, exclusive"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    login_user: UserPayload = Depends(UserPayload.get_login_user),
):
    query = AgentTransactionQuery(
        app_id=app_id,
        app_type=app_type,
        status=status,
        keyword=keyword,
        start_time=start_time,
        end_time=end_time,
        page=page,
        page_size=page_size,
    )
    result = await AgentTransactionsService.query_transactions(login_user, query)
    return resp_200(data=result)


@router.get("/{trace_id}/nodes", summary="Node execution records of one run (merged start/end)")
async def get_transaction_nodes(
    trace_id: str,
    request: Request,
    login_user: UserPayload = Depends(UserPayload.get_login_user),
):
    result = await AgentTransactionsService.get_transaction_nodes(login_user, trace_id)
    return resp_200(data=result)


@router.get("/{trace_id}/model-calls", summary="Model invocations of one run (trace_id linked)")
async def get_transaction_model_calls(
    trace_id: str,
    request: Request,
    login_user: UserPayload = Depends(UserPayload.get_login_user),
):
    result = await AgentTransactionsService.get_transaction_model_calls(login_user, trace_id)
    return resp_200(data=result)
