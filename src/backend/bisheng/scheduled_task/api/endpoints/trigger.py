"""F077 事件触发端点 `POST /api/v2/schedule/trigger`（design 决策 12）。

外部系统持服务账号密钥触发一条已配置好的定时任务。三条硬边界：

1. **恒强制密钥**：本端点纯新增、没有旧行为可兼容，所以即便部署总闸
   (`open_api.enforce_keys`) 关着（F076 依赖此时是 no-op），端点自身仍要求已校验
   的 principal（否则 401/11801）。启用事件触发的前置条件 = 打开总闸。
2. **载荷白名单**：只收 `task_id` + 可选 `inputs`；多余字段 400/11916
   （裸 `user_id` 由 F076 依赖更早拦下 → 400/11817）。**禁止指定执行身份/目标应用/
   触发计划**——执行身份在任务上锁定（INV-25）。
3. **防枚举**：任务不存在 / 不在密钥的授权范围 / 已停用 → 一律 404/11809，
   三种情形不可区分（与 F076 同口径）。

受理即返回 `{task_id, run_trace_id}`，不等运行完成；**不提供外部运行查询接口**
（对客差异②，运行结果看平台任务列表/监控）。
"""

from __future__ import annotations

from fastapi import APIRouter, Request
from pydantic import BaseModel

from bisheng.common.errcode.open_api import OpenApiKeyMissingError, OpenApiNotFoundError
from bisheng.common.schemas.api import resp_200
from bisheng.open_api.domain.agent_scope import assert_open_api_agent_allowed
from bisheng.open_api.domain.context import get_open_api_principal
from bisheng.open_api.domain.scopes import open_api_scope
from bisheng.permission.domain.services.application_permission_service import (
    ApplicationPermissionService,
)
from bisheng.scheduled_task.domain.models.dao import ScheduledTaskDao
from bisheng.scheduled_task.domain.models.scheduled_task import TaskStatus
from bisheng.scheduled_task.domain.services import run_service, trigger_guard
from bisheng.scheduled_task.domain.services.run_planner import TriggerKind

router = APIRouter(prefix="/schedule", tags=["OpenApi"])

#: 载荷白名单（AC-35）：只接受这两个键。
_ALLOWED_BODY_FIELDS = frozenset({"task_id", "inputs"})


class TriggerRequest(BaseModel):
    """触发请求体。``inputs`` 目前只做透传占位（一期预设输入在任务上配置）。"""

    task_id: int
    inputs: dict | None = None


@router.post("/trigger")
@open_api_scope("schedule:trigger")
async def trigger_scheduled_task(payload: TriggerRequest, request: Request):
    principal = get_open_api_principal()
    if principal is None:
        # 总闸关闭时依赖不装 principal；事件触发不接受匿名（没有旧行为要兼容）。
        raise OpenApiKeyMissingError()

    # 白名单校验用原始 JSON（Starlette 已缓存 body，F076 依赖读过一次，这里不重复 IO）。
    raw = await request.json()
    extra = set(raw) - _ALLOWED_BODY_FIELDS if isinstance(raw, dict) else set()
    if extra:
        from bisheng.common.errcode.scheduled_task import ScheduledTaskTriggerPayloadInvalidError

        raise ScheduledTaskTriggerPayloadInvalidError(exception=f"unexpected fields: {sorted(extra)}")

    # 防枚举：任务缺失（含跨租户，租户过滤后查不到）、停用、或不在密钥授权范围 → 404。
    task = await ScheduledTaskDao.aget(payload.task_id)
    if task is None or task.status != TaskStatus.ENABLED.value:
        raise OpenApiNotFoundError()

    object_type = trigger_guard.object_type_of(task.app_type)
    if object_type is None:
        raise OpenApiNotFoundError()
    # F076 增量(2026-10-22):定时触发是"密钥 → 任务 → 智能体"的间接调用,同样受
    # 每 Key 智能体白名单约束;与资源不可见同码(11809)防枚举。
    assert_open_api_agent_allowed(object_type, str(task.app_id))
    if not await ApplicationPermissionService.has_any_permission_async(
        principal.owner_payload, object_type, str(task.app_id), ["use_app"]
    ):
        # 授权范围口径（design 决策 12）：密钥所属服务账号对任务的目标应用有运行权限。
        raise OpenApiNotFoundError()

    # T-19：防重放（时间窗 + 一次性随机数）与任务级频率上限，都在产生运行之前。
    await run_service.assert_not_replayed(_header_int(request, "x-bs-timestamp"), request.headers.get("x-bs-nonce"))
    await run_service.consume_trigger_quota(task.id)

    trace_id = await run_service.run_now(task.id, trigger_kind=TriggerKind.EVENT)
    return resp_200({"task_id": task.id, "run_trace_id": trace_id})


def _header_int(request: Request, name: str) -> int | None:
    raw = request.headers.get(name)
    if raw is None:
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None
