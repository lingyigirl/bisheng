"""Opt-in recovery evidence and stale-writer checks against local real services.

Set F074_RECOVERY_AUTH_FILE and F074_RECOVERY_TASK_ID to a completed crash-tested
task. Use the matching backend config. The SQL write is rejected and rolled back;
the only Redis mutation is an owned, temporary lease on a terminal test task.
"""

import json
import os
from pathlib import Path
from uuid import uuid4

import httpx
import pytest
from redis import Redis
from redis.asyncio import Redis as AsyncRedis
from sqlalchemy import create_engine, update
from sqlalchemy.orm import Session

from bisheng.core.context.tenant import current_tenant_id, set_current_tenant_id
from bisheng.linsight.domain.models.linsight_session_version import LinsightSessionVersion
from bisheng.linsight.domain.services.recovery_context import ExecutionFence, RecoveryLeaseLost, execution_fence
from bisheng.linsight.domain.services.recovery_service import _RELEASE


@pytest.fixture
def live_task():
    auth = os.getenv("F074_RECOVERY_AUTH_FILE")
    task_id = os.getenv("F074_RECOVERY_TASK_ID")
    if not auth or not task_id:
        pytest.skip("An explicit completed recovery test task is required")
    cookies = json.loads(Path(auth).read_text(encoding="utf-8"))["cookies"]
    response = httpx.get(
        "http://127.0.0.1:7860/api/v1/linsight/workbench/replay-snapshot",
        params={"session_version_id": task_id},
        cookies=cookies,
        timeout=30,
    )
    response.raise_for_status()
    snapshot = response.json()["data"]
    assert snapshot["version"]["status"] == "completed"
    assert snapshot["version"]["recovery_epoch"] >= 2
    return snapshot


def test_real_worker_recovered_same_task_once(live_task):
    events = live_task["snapshot"]["events"]
    recoveries = [event for event in events if event["event_type"] == "task_recovery"]
    assert len(recoveries) == 1
    assert recoveries[0]["data"]["epoch"] == 2
    assert events[-1]["event_type"] == "final_result"
    assert events[-1]["data"]["id"] == live_task["version"]["id"]
    assert live_task["version"]["output_result"]["answer"]


async def test_real_mysql_rejects_old_epoch_even_with_valid_redis_token(live_task):
    task = live_task["version"]
    database_url = os.getenv("F074_RECOVERY_DB_URL")
    if not database_url:
        pytest.skip("Explicit test database URL required")
    engine = create_engine(database_url)
    redis_url = os.getenv("F074_RECOVERY_REDIS_URL", "redis://127.0.0.1:6379/1")
    redis, sync_redis = AsyncRedis.from_url(redis_url), Redis.from_url(redis_url)
    fence = ExecutionFence(task["id"], task["tenant_id"], uuid4().hex, task["recovery_epoch"] - 1, redis, sync_redis)
    assert await fence.redis.set(fence.lease_key, fence.token, nx=True, ex=20), "Test task is still leased"
    tenant_token = set_current_tenant_id(task["tenant_id"])
    token = execution_fence.set(fence)
    try:
        with Session(engine) as session:
            with pytest.raises(RecoveryLeaseLost):
                session.execute(
                    update(LinsightSessionVersion)
                    .where(
                        LinsightSessionVersion.id == task["id"],
                        LinsightSessionVersion.tenant_id == task["tenant_id"],
                    )
                    .values(title="must-not-be-written")
                )
            session.rollback()
    finally:
        execution_fence.reset(token)
        current_tenant_id.reset(tenant_token)
        await fence.redis.eval(_RELEASE, 1, fence.lease_key, fence.token)
        await redis.aclose()
        sync_redis.close()
        engine.dispose()
