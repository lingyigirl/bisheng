"""In-process HTTP contract tests; external database/provider E2E is separate."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI
from fastapi.responses import JSONResponse

from bisheng.common.dependencies.user_deps import UserPayload
from bisheng.common.errcode.base import BaseErrorCode
from bisheng.core.context.tenant import current_tenant_id, set_current_tenant_id
from bisheng.linsight.api.endpoints import linsight
from bisheng.linsight.domain.repositories.agent_config_repository import AgentConfigRepository
from bisheng.linsight.domain.services.event_replay import EventReplayService

fakeredis = pytest.importorskip("fakeredis.aioredis")


async def test_snapshot_endpoint_checks_owner_and_preserves_stream_cursor(monkeypatch):
    """AC-12, AC-13, AC-19: real route and authorization with an emulated Redis."""
    redis = fakeredis.FakeRedis()
    token = set_current_tenant_id(12)
    try:
        version = SimpleNamespace(
            id="task", user_id=8, tenant_id=12, status="in_progress", agent_config={"event_protocol": 2}
        )
        version.model_dump = lambda: {"id": "task", "status": "in_progress", "agent_config": version.agent_config}
        monkeypatch.setattr(AgentConfigRepository, "task", AsyncMock(return_value=version))
        monkeypatch.setattr(
            linsight, "get_redis_client", AsyncMock(return_value=SimpleNamespace(async_connection=redis))
        )
        replay = EventReplayService(redis, 12, "task")
        cursor = await replay.append({"event_type": "replay_init", "data": {}}, "init")
        app = FastAPI()
        app.include_router(linsight.router, prefix="/api/v1/linsight")

        @app.exception_handler(BaseErrorCode)
        async def handle_error(request, error):
            return JSONResponse({"status_code": error.code, "status_message": error.message, "data": None})

        user = SimpleNamespace(user_id=8)
        app.dependency_overrides[UserPayload.get_login_user] = lambda: user
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get(
                "/api/v1/linsight/workbench/replay-snapshot", params={"session_version_id": "task"}
            )
            body = response.json()
            assert response.status_code == 200
            assert body["status_code"] == 200
            assert body["data"]["last_event_id"] == cursor
            assert body["data"]["complete_history"] is True
            user.user_id = 9
            denied = (
                await client.get("/api/v1/linsight/workbench/replay-snapshot", params={"session_version_id": "task"})
            ).json()
            from bisheng.common.errcode.http_error import UnAuthorizedError

            assert denied["status_code"] == UnAuthorizedError.Code
    finally:
        await redis.aclose()
        current_tenant_id.reset(token)
