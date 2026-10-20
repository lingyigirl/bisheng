"""F077 T-18/T-19：事件触发端点（鉴权前置 / 载荷白名单 / 防枚举 / 防重放 / 频控）。

按仓内 076 先例直接调用端点函数（依赖注入在测试里显式给桩）：principal 用
ContextVar 装上，Redis 用内存假件，`run_now` 用桩——本文件锁的是**入口语义**，
运行本身的行为已由 T-10 的编排测试覆盖。
"""

import json
from datetime import datetime

import pytest
from starlette.requests import Request

from bisheng.common.errcode.open_api import (
    OpenApiAuthBackendUnavailableError,
    OpenApiKeyMissingError,
    OpenApiNotFoundError,
)
from bisheng.common.errcode.scheduled_task import (
    ScheduledTaskAlreadyRunningError,
    ScheduledTaskReplayRejectedError,
    ScheduledTaskTriggerPayloadInvalidError,
    ScheduledTaskTriggerRateLimitedError,
)
from bisheng.open_api.domain.context import (
    OpenApiPrincipal,
    _current_principal,
    reset_open_api_principal,
    set_open_api_principal,
)
from bisheng.scheduled_task.api.endpoints import trigger as endpoint
from bisheng.scheduled_task.domain.models.scheduled_task import ScheduledTask, TaskStatus
from bisheng.scheduled_task.domain.services import run_service
from bisheng.scheduled_task.domain.services.run_planner import TriggerKind

TENANT_ID = 7


class _FakeRedisConn:
    def __init__(self, *, accept_nonce=True, incr_result=1):
        self.accept_nonce = accept_nonce
        self.incr_result = incr_result
        self.expires = []

    async def set(self, key, value, nx=True, ex=None):
        return self.accept_nonce

    async def incr(self, key):
        return self.incr_result

    async def expire(self, key, ttl):
        self.expires.append((key, ttl))
        return True


class _FakeRedis:
    def __init__(self, conn):
        self.async_connection = conn


class _Payload:
    is_global_super = False

    async def has_tenant_admin(self, tenant_id):
        return False


async def _request(body: dict, headers: dict | None = None) -> Request:
    data = json.dumps(body).encode()
    raw_headers = [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()]
    raw_headers.append((b"content-type", b"application/json"))

    async def receive():
        return {"type": "http.request", "body": data, "more_body": False}

    scope = {
        "type": "http",
        "method": "POST",
        "path": "/api/v2/schedule/trigger",
        "headers": raw_headers,
        "query_string": b"",
    }
    return Request(scope, receive)


def _task(**overrides) -> ScheduledTask:
    base = {
        "id": 1,
        "tenant_id": TENANT_ID,
        "name": "nightly",
        "app_id": "1001",
        "app_type": 10,
        "service_account_id": 5,
        "run_identity_user_id": 501,
        "created_by": 42,
        "status": TaskStatus.ENABLED.value,
        "cron_expression": "0 9 * * *",
        "next_run_at": datetime(2026, 10, 20, 9, 0),
    }
    base.update(overrides)
    return ScheduledTask(**base)


@pytest.fixture(autouse=True)
def principal():
    token = set_open_api_principal(
        OpenApiPrincipal(
            credential_id=1,
            tenant_id=TENANT_ID,
            service_account_id=5,
            owner_user_id=501,
            key_mask="bs-sak-****abcd",
            scopes=("schedule:trigger",),
            owner_payload=_Payload(),
        )
    )
    yield
    reset_open_api_principal(token)


@pytest.fixture()
def wire(monkeypatch):
    state = {"task": _task(), "use_app": True, "redis": _FakeRedisConn(), "traces": []}

    async def _aget(task_id):
        return state["task"]

    async def _has_perm(login_user, object_type, object_id, permission_ids):
        return state["use_app"]

    async def _redis():
        return _FakeRedis(state["redis"])

    async def _run_now(task_id, *, trigger_kind=TriggerKind.MANUAL, dispatch=None):
        state["traces"].append((task_id, trigger_kind))
        return "trace-event-1"

    monkeypatch.setattr(endpoint.ScheduledTaskDao, "aget", _aget)
    monkeypatch.setattr(endpoint.ApplicationPermissionService, "has_any_permission_async", _has_perm)
    monkeypatch.setattr(run_service, "get_redis_client", _redis)
    monkeypatch.setattr(run_service, "run_now", _run_now)
    return state


def _ts(offset: int = 0) -> dict:
    import time

    return {
        "X-Bs-Timestamp": str(int(time.time()) + offset),
        "X-Bs-Nonce": "nonce-1",
    }


async def test_requires_principal_even_when_switch_off():
    """总闸关闭时依赖是 no-op → 端点自身仍拒绝匿名（恒强制密钥）。"""
    # 直接清空 ContextVar（模拟"依赖未装 principal"的分支），测试结束还原。
    token = _current_principal.set(None)
    try:
        with pytest.raises(OpenApiKeyMissingError):
            await endpoint.trigger_scheduled_task(
                endpoint.TriggerRequest(task_id=1), await _request({"task_id": 1}, _ts())
            )
    finally:
        _current_principal.reset(token)


async def test_rejects_fields_outside_whitelist(wire):
    req = await _request({"task_id": 1, "app_id": "other"}, _ts())
    with pytest.raises(ScheduledTaskTriggerPayloadInvalidError) as excinfo:
        await endpoint.trigger_scheduled_task(endpoint.TriggerRequest(task_id=1), req)
    assert excinfo.value.Code == 11916


async def test_not_found_is_blind_for_missing_disabled_and_unauthorized(wire):
    req = await _request({"task_id": 1}, _ts())

    wire["task"] = None
    with pytest.raises(OpenApiNotFoundError):
        await endpoint.trigger_scheduled_task(endpoint.TriggerRequest(task_id=1), req)

    wire["task"] = _task(status=TaskStatus.DISABLED.value)
    with pytest.raises(OpenApiNotFoundError):
        await endpoint.trigger_scheduled_task(endpoint.TriggerRequest(task_id=1), req)

    wire["task"] = _task()
    wire["use_app"] = False
    with pytest.raises(OpenApiNotFoundError):
        await endpoint.trigger_scheduled_task(endpoint.TriggerRequest(task_id=1), req)


async def test_replay_guard_rejects_missing_stale_and_reused(wire):
    req_ok = await _request({"task_id": 1}, _ts())

    # 缺头
    with pytest.raises(ScheduledTaskReplayRejectedError):
        await endpoint.trigger_scheduled_task(endpoint.TriggerRequest(task_id=1), await _request({"task_id": 1}))

    # 超窗（±5 分钟默认）
    with pytest.raises(ScheduledTaskReplayRejectedError):
        await endpoint.trigger_scheduled_task(
            endpoint.TriggerRequest(task_id=1), await _request({"task_id": 1}, _ts(offset=3600))
        )

    # nonce 已用过
    wire["redis"].accept_nonce = False
    with pytest.raises(ScheduledTaskReplayRejectedError):
        await endpoint.trigger_scheduled_task(endpoint.TriggerRequest(task_id=1), req_ok)


async def test_rate_limit_guard(wire):
    wire["redis"].incr_result = 11  # 默认上限 10 次/分钟
    with pytest.raises(ScheduledTaskTriggerRateLimitedError):
        await endpoint.trigger_scheduled_task(endpoint.TriggerRequest(task_id=1), await _request({"task_id": 1}, _ts()))


async def test_redis_unavailable_is_503(wire, monkeypatch):
    async def _boom():
        raise RuntimeError("redis down")

    monkeypatch.setattr(run_service, "get_redis_client", _boom)
    with pytest.raises(OpenApiAuthBackendUnavailableError):
        await endpoint.trigger_scheduled_task(endpoint.TriggerRequest(task_id=1), await _request({"task_id": 1}, _ts()))


async def test_happy_path_returns_trace_and_uses_event_semantics(wire):
    result = await endpoint.trigger_scheduled_task(
        endpoint.TriggerRequest(task_id=1), await _request({"task_id": 1}, _ts())
    )
    assert result.data == {"task_id": 1, "run_trace_id": "trace-event-1"}
    assert wire["traces"] == [(1, TriggerKind.EVENT)], "事件触发走 EVENT 语义（不改计划、不计失败）"


async def test_overlap_maps_to_11910_for_events(wire):
    async def _overlap(task_id, *, trigger_kind=TriggerKind.MANUAL, dispatch=None):
        from bisheng.scheduled_task.domain.services.run_planner import PlanDecision, PlanReason, PlanVariant

        raise run_service._rejection_error(
            PlanDecision(variant=PlanVariant.REJECT, trigger_kind=trigger_kind, reason=PlanReason.OVERLAP),
            trigger_kind,
        )

    import bisheng.scheduled_task.domain.services.run_service as rs

    # 端点调的是 run_service.run_now（已 patch）；这里改为验证错误映射本身
    from bisheng.scheduled_task.domain.services.run_planner import PlanDecision, PlanReason, PlanVariant

    err = rs._rejection_error(
        PlanDecision(variant=PlanVariant.REJECT, trigger_kind=TriggerKind.EVENT, reason=PlanReason.OVERLAP),
        TriggerKind.EVENT,
    )
    assert isinstance(err, ScheduledTaskAlreadyRunningError)
    assert err.Code == 11910
