"""智能体发布审批：发布(上线)网关触发 + outbox 执行体。

触发入口（仅 ONLINE 方向，下线不经审批）：
  - PATCH /api/v1/workflow/status（编辑器页头「上线」，版本级）
  - PATCH /api/v1/workflow/update/{flow_id}（应用列表开关，status=ONLINE）
  - POST /api/v1/assistant/status（助手上线）

口径：审批场景未配置/停用 → **直接发布**（fail-open，用户明确要求）；
场景配置异常（路由缺失/审批人为空）→ 阻止发布并暴露错误（管理员已被网关通知）。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from bisheng.approval.domain.schemas.approval_center_schema import ApprovalGateDecision, ApprovalGateRequest
from bisheng.approval.domain.services.agent_publish_handler import AgentPublishApprovalHandler
from bisheng.approval.domain.services.approval_gate import ApprovalGate
from bisheng.approval.domain.services.approval_registry import ApprovalRegistry
from bisheng.common.dependencies.user_deps import UserPayload
from bisheng.common.errcode.approval import (
    ApprovalScenarioDisabledError,
    ApprovalScenarioExceptionError,
)
from bisheng.utils import get_request_ip


@dataclass
class PublishGateOutcome:
    publish_now: bool = True
    pending: bool = False
    instance_id: int | None = None
    task_ids: list[int] = field(default_factory=list)


def _build_publish_gate() -> ApprovalGate:
    registry = ApprovalRegistry.with_default_presets()
    registry.register_handler(
        'agent_publish_request',
        AgentPublishApprovalHandler(publish_app=publish_app_by_snapshot),
    )
    return ApprovalGate(registry=registry)


async def gate_agent_publish(
    login_user: UserPayload,
    *,
    app_type: str,
    app_id: str,
    business_name: str,
    version_id: int | None = None,
    request=None,
) -> PublishGateOutcome:
    """发布前过审批网关。场景未配置/停用 → publish_now=True（fail-open）。"""
    from bisheng.database.models.department import UserDepartmentDao

    primary_dept = await UserDepartmentDao.aget_user_primary_department(login_user.user_id)
    payload_snapshot = {
        'app_type': app_type,
        'app_id': str(app_id),
        'version_id': version_id,
        'applicant_user_id': login_user.user_id,
    }
    if app_type == 'assistant':
        payload_snapshot['assistant_id'] = str(app_id)
    else:
        payload_snapshot['flow_id'] = str(app_id)

    gate = _build_publish_gate()
    try:
        result = await gate.request_or_pass(
            ApprovalGateRequest(
                tenant_id=login_user.tenant_id,
                scenario_code='agent_publish_request',
                business_key=f'app_publish:{app_type}:{app_id}',
                business_resource_type=app_type,
                business_resource_id=str(app_id),
                business_name=business_name,
                applicant_user_id=login_user.user_id,
                applicant_user_name=getattr(login_user, 'user_name', str(login_user.user_id)),
                applicant_department_id=primary_dept.department_id if primary_dept else None,
                payload_snapshot=payload_snapshot,
                ip_address=get_request_ip(request) if request else None,
            )
        )
    except ApprovalScenarioDisabledError:
        # 场景未配置/停用: 无需审批, 直接发布
        return PublishGateOutcome(publish_now=True)

    if result.decision == ApprovalGateDecision.PASS:
        return PublishGateOutcome(publish_now=True)
    if result.decision == ApprovalGateDecision.PENDING:
        await _notify_publish_approvers(
            login_user=login_user,
            instance_id=result.instance_id,
            task_ids=result.task_ids,
            business_name=business_name,
        )
        return PublishGateOutcome(
            publish_now=False,
            pending=True,
            instance_id=result.instance_id,
            task_ids=result.task_ids,
        )
    # EXCEPTION（route_missing / approver_empty）: 阻止发布, 管理员已被网关通知
    raise ApprovalScenarioExceptionError()


async def _notify_publish_approvers(*, login_user, instance_id: int, task_ids: list[int], business_name: str) -> None:
    from bisheng.approval.domain.repositories.approval_instance_repository import ApprovalInstanceRepository
    from bisheng.approval.domain.services.approval_notification_service import ApprovalNotificationService

    approver_user_ids: list[int] = []
    seen: set[int] = set()
    for task_id in task_ids:
        task = await ApprovalInstanceRepository.get_task(task_id)
        if task and task.approver_user_id not in seen:
            seen.add(task.approver_user_id)
            approver_user_ids.append(task.approver_user_id)
    if not approver_user_ids:
        return
    await ApprovalNotificationService.notify_users(
        sender=login_user.user_id,
        receiver_user_ids=approver_user_ids,
        action_code='approval_task_pending',
        business_name=business_name,
        instance_id=instance_id,
        scenario_code='agent_publish_request',
        task_id=task_ids[0],
    )


async def publish_app_by_snapshot(payload_snapshot: dict) -> None:
    """outbox 执行体：审批通过后真正执行发布（不静默失败原则，缺失/失败必须 raise）。

    执行期不做权限校验——提交时的权限已由网关入口校验、发布动作本身已被审批。
    """
    app_type = payload_snapshot.get('app_type')
    if app_type == 'assistant':
        await _publish_assistant_snapshot(payload_snapshot)
    elif app_type == 'workflow':
        await _publish_workflow_snapshot(payload_snapshot)
    else:
        raise ValueError(f"unknown app_type={app_type!r} in publish payload_snapshot")


async def _publish_workflow_snapshot(snapshot: dict) -> None:
    flow_id = snapshot.get('flow_id') or snapshot.get('app_id')
    version_id = snapshot.get('version_id')
    if not flow_id:
        raise ValueError(f'publish snapshot missing flow_id: {snapshot}')

    from bisheng.common.errcode.http_error import NotFoundError
    from bisheng.database.models.flow import FlowDao, FlowStatus
    from bisheng.database.models.flow_version import FlowVersionDao

    db_flow = await FlowDao.aget_flow_by_id(flow_id)
    if not db_flow:
        raise NotFoundError()
    # 版本级发布: 校验版本 + 图初始化 + 切换当前版本 (与 WorkFlowService.update_flow_status 同口径)
    if version_id is not None:
        version_info = await FlowVersionDao.aget_version_by_id(version_id)
        if not version_info or version_info.flow_id != flow_id:
            raise NotFoundError()
        from bisheng.workflow.graph.workflow import Workflow

        _ = Workflow(
            flow_id,
            db_flow.name,
            snapshot.get('applicant_user_id') or db_flow.user_id,
            version_info.data,
            False,
            10,
            10,
            None,
        )
        await FlowVersionDao.change_current_version(flow_id, version_info)
    db_flow.status = FlowStatus.ONLINE.value
    await FlowDao.aupdate_flow(db_flow)
    await _log_publish_telemetry(snapshot.get('applicant_user_id'))


async def _publish_assistant_snapshot(snapshot: dict) -> None:
    assistant_id = snapshot.get('assistant_id') or snapshot.get('app_id')
    if not assistant_id:
        raise ValueError(f'publish snapshot missing assistant_id: {snapshot}')

    from bisheng.common.errcode.assistant import AssistantNotExistsError
    from bisheng.database.models.assistant import AssistantDao, AssistantStatus

    assistant = AssistantDao.get_one_assistant(assistant_id)
    if not assistant:
        raise AssistantNotExistsError()
    # 上线前初始化校验 (与 AssistantService.update_status 同口径)
    from bisheng.api.services.assistant_agent import AssistantAgent

    tmp_agent = AssistantAgent(assistant, '', snapshot.get('applicant_user_id') or assistant.user_id)
    await tmp_agent.init_assistant()
    assistant.status = AssistantStatus.ONLINE.value
    AssistantDao.update_assistant(assistant)
    await _log_publish_telemetry(snapshot.get('applicant_user_id'))


async def _log_publish_telemetry(user_id: int | None) -> None:
    from bisheng.common.constants.enums.telemetry import BaseTelemetryTypeEnum
    from bisheng.common.services import telemetry_service
    from bisheng.core.logger import trace_id_var

    await telemetry_service.log_event(
        user_id=user_id,
        event_type=BaseTelemetryTypeEnum.EDIT_APPLICATION,
        trace_id=trace_id_var.get(),
    )
