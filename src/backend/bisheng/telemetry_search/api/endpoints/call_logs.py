"""F046: agent-run / model-call logs (read-only, raw telemetry events)."""

from fastapi import APIRouter, Depends, Query, Request

from bisheng.api.v1.schemas import resp_200
from bisheng.common.dependencies.user_deps import UserPayload
from bisheng.telemetry_search.domain.schemas.call_logs import CallLogQuery
from bisheng.telemetry_search.domain.services.call_logs import CallLogsService

router = APIRouter(prefix="/call-logs", tags=["TelemetryCallLogs"])


@router.get("", summary="Query agent-run and model-call logs")
async def get_call_logs(
    request: Request,
    record_type: str = Query(default=None, description="agent_run | model_call; empty returns both"),
    app_id: str = Query(default=None),
    status: str = Query(default=None, description="success | failed"),
    start_time: int = Query(default=None, description="Epoch seconds, inclusive"),
    end_time: int = Query(default=None, description="Epoch seconds, exclusive"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    login_user: UserPayload = Depends(UserPayload.get_login_user),
):
    query = CallLogQuery(
        record_type=record_type,
        app_id=app_id,
        status=status,
        start_time=start_time,
        end_time=end_time,
        page=page,
        page_size=page_size,
    )
    result = CallLogsService.query_call_logs(login_user, query)
    return resp_200(data=result)
