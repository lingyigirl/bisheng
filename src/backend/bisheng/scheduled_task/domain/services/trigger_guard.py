"""F077 触发前复检（design 决策 9）——**每次触发都要做，不得只在创建时校验一次**。

三处入口共用这一个函数：beat 扫描（计划轮次）、`POST /api/v2/schedule/trigger`
（事件触发）、`POST /api/v1/scheduled-tasks/{id}/run`（立即运行）。它只**判定与给原因**，
不做任何写库——写回统一落在 DAO 的原子 claim 上（design 决策 4）。

四查（spec AC-21）：

1. 目标应用存在且**上线**（下线即不执行，是对客登记的新规则——与既有 `/api/v2` 调用面
   不校验上线状态不同）；
2. 运行身份所属服务账号仍可用（存在 / 未停用 / 未软删）；
3. 该账号的 owner 与任务锁定的运行身份**仍是同一个**（换 owner 后的旧身份不再是任何
   服务账号的专用身份 → 一律 fail-closed，编辑任务换身份才能恢复）；
4. owner 可装载、未被提权，且对目标应用仍持 `use_app`。

**撤权生效窗口 = 最坏 10 秒**（AC-23）：判定链复用 `PermissionService`，撤权会同步失效
其缓存，绕过失效路径的变动由 L2 TTL=10s 兜底。本模块**不自建缓存**（叠加第二个 TTL 会
直接击穿 10 秒上界）。

**注意两个窗口的区别**：判定生效 ≤10s；**已经派发出去的一轮不中断**（与 F076「吊销不
中断已建立会话」同精神，对客文档写明）。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from loguru import logger

from bisheng.database.models.assistant import AssistantDao, AssistantStatus
from bisheng.database.models.flow import FlowDao, FlowStatus, FlowType
from bisheng.open_api.domain.models.dao import ServiceAccountDao
from bisheng.open_api.domain.services.identity_service import load_owner_payload, owner_is_privileged
from bisheng.permission.domain.services.application_permission_service import ApplicationPermissionService
from bisheng.scheduled_task.domain.models.scheduled_task import ScheduledTask

#: 应用在权限判定里的资源类型（先例 `common/chat/manager.py:196`）。
_OBJECT_TYPE_BY_APP_TYPE: dict[int, str] = {
    FlowType.ASSISTANT.value: "assistant",
    FlowType.WORKFLOW.value: "workflow",
}


class AppState(StrEnum):
    """目标应用的可运行状态（`UNSUPPORTED` 归入 `DELETED` 语义：跑不了等于不存在）。"""

    OK = "ok"
    OFFLINE = "offline"
    DELETED = "deleted"


class IdentityState(StrEnum):
    """运行身份的可运行状态；除 `OK` 外一律 fail-closed，且**不回退为任何其他身份**。"""

    OK = "ok"
    ACCOUNT_UNAVAILABLE = "account_unavailable"  # 账号不存在 / 已停用 / 已软删
    OWNER_CHANGED = "owner_changed"  # 账号换了 owner，锁定的身份不再是它的专用身份
    OWNER_UNAVAILABLE = "owner_unavailable"  # owner 被停用 / 租户成员失效 / 归属漂移
    OWNER_PRIVILEGED = "owner_privileged"  # owner 事后被提权（INV-23 ②）
    NO_USE_APP = "no_use_app"  # 对目标应用的运行权限被撤销


@dataclass(frozen=True)
class RecheckOutcome:
    """复检结论。

    `payload` 在身份可用时回带，供派发方直接以该身份执行（省一次装载）；
    复检失败时它是 `None`——**调用方不得据此回退到别的身份**。
    """

    app_state: AppState
    identity_state: IdentityState
    payload: object | None = None

    @property
    def app_ok(self) -> bool:
        return self.app_state is AppState.OK

    @property
    def identity_ok(self) -> bool:
        return self.identity_state is IdentityState.OK

    @property
    def ok(self) -> bool:
        return self.app_ok and self.identity_ok


async def _app_state(task: ScheduledTask) -> AppState:
    """应用存在性与上线状态。工作流硬删（行没了即已删除）、助手软删（`is_delete`）。"""
    if task.app_type == FlowType.ASSISTANT.value:
        row = await AssistantDao.aget_one_assistant(str(task.app_id))
        if row is None or int(getattr(row, "is_delete", 0) or 0) != 0:
            return AppState.DELETED
        return AppState.OK if row.status == AssistantStatus.ONLINE.value else AppState.OFFLINE

    if task.app_type == FlowType.WORKFLOW.value:
        row = await FlowDao.aget_flow_by_id(str(task.app_id))
        if row is None or row.flow_type != FlowType.WORKFLOW.value:
            return AppState.DELETED
        return AppState.OK if row.status == FlowStatus.ONLINE.value else AppState.OFFLINE

    # 不在可定时化的两型之内（AC-47 的创建期校验本应拦住，旁路数据在此 fail-closed）
    return AppState.DELETED


async def recheck_task(task: ScheduledTask) -> RecheckOutcome:
    """四查入口。**调用方必须先 `set_current_tenant_id(task.tenant_id)`**——
    服务账号按租户收窄查找（跨租户查不到），权限判定也依赖当前租户上下文。
    """
    app_state = await _app_state(task)
    tenant_id = task.tenant_id

    account = await ServiceAccountDao.aget_scoped(task.service_account_id, tenant_id=tenant_id)
    if account is None or not account.is_enabled:
        return RecheckOutcome(app_state, IdentityState.ACCOUNT_UNAVAILABLE)
    if account.resource_owner_user_id != task.run_identity_user_id:
        # 账号换了 owner：任务锁定的身份不再是它的专用身份，必须由编辑任务来换。
        return RecheckOutcome(app_state, IdentityState.OWNER_CHANGED)

    payload = await load_owner_payload(owner_user_id=task.run_identity_user_id, tenant_id=tenant_id)
    if payload is None:
        return RecheckOutcome(app_state, IdentityState.OWNER_UNAVAILABLE)
    if await owner_is_privileged(payload, tenant_id):
        return RecheckOutcome(app_state, IdentityState.OWNER_PRIVILEGED)

    object_type = _OBJECT_TYPE_BY_APP_TYPE.get(task.app_type)
    if object_type is None:  # pragma: no cover - 与 _app_state 的不支持分支同源
        return RecheckOutcome(app_state, IdentityState.NO_USE_APP)

    has_use_app = await ApplicationPermissionService.has_any_permission_async(
        payload, object_type, str(task.app_id), ["use_app"]
    )
    if not has_use_app:
        return RecheckOutcome(app_state, IdentityState.NO_USE_APP)

    return RecheckOutcome(app_state, IdentityState.OK, payload=payload)


def describe_failure(outcome: RecheckOutcome) -> str:
    """给任务行 `last_error` 的短原因（只存标识与摘要，不含任何正文；AC-51）。"""
    if outcome.app_ok and outcome.identity_ok:
        return ""
    parts = []
    if not outcome.app_ok:
        parts.append(f"app_{outcome.app_state.value}")
    if not outcome.identity_ok:
        parts.append(outcome.identity_state.value)
    text = ",".join(parts)
    logger.debug("scheduled_task: recheck failed ({})", text)
    return text
