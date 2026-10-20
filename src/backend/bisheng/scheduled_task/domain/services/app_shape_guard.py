"""F077 应用可定时化的形状校验（spec AC-47）。

一期只支持"单个人工输入节点 + 无文件类输入项"的应用：

- **多于一个人工输入节点**：运行会连续暂停两次，而 F054 协议只处理一轮 INPUT
  （`evaluation_service.py:483-484` 的二次 INPUT 会被当作结束并 stop），监督过没有
  第二条答复通道；
- **文件类输入项**：无人值守没有文件来源可言（预设输入只存字符串），而以"跳过该
  文件项"继续跑会让运行"成功"却少了内容——比直接拒绝更糟。

两类都在**创建任务时**拒绝（11914），而不是在运行期才暴露。助手应用没有输入节点，
恒可定时化。
"""

from __future__ import annotations

from loguru import logger

from bisheng.common.errcode.scheduled_task import ScheduledTaskAppNotSchedulableError
from bisheng.database.models.flow import FlowDao, FlowType
from bisheng.managed_agent.domain.constants import MANAGED_AGENT_APP_TYPE

#: 输入节点的 `tab` 取值；`dialog_input` 是对话式输入（无文件项定义）。
_FORM_TAB = "form_input"


def input_nodes(data: dict) -> list[dict]:
    """收集 `flow.data` 里的人工输入节点（纯函数，可直接单测）。

    返回 ``[{"tab": str, "form_items": [ {...}, ... ]}]``；`form_items` 只在
    ``form_input`` 形态下有值（`node_params["form_input"]` 是字段定义列表）。
    """
    nodes: list[dict] = []
    for node in (data or {}).get("nodes") or []:
        if node.get("type") != "input":
            continue
        params: dict = {}
        for group in (node.get("data") or {}).get("group_params") or []:
            for param in group.get("params") or []:
                params[param.get("key")] = param.get("value")
        nodes.append(
            {
                "tab": params.get("tab") or "",
                "form_items": params.get("form_input") or [],
            }
        )
    return nodes


def describe_unschedulable(data: dict) -> str:
    """返回不可定时化的原因（空串 = 可定时化）。"""
    nodes = input_nodes(data)
    if len(nodes) > 1:
        return f"multiple input nodes: {len(nodes)}"
    for node in nodes:
        if node["tab"] != _FORM_TAB:
            continue
        for item in node["form_items"]:
            if isinstance(item, dict) and item.get("type") == "file":
                return "file input is not supported on schedules"
    return ""


async def assert_app_schedulable(app_id: str, app_type: int) -> None:
    """工作流：按 `flow.data` 校验；助手：恒通过（无输入节点概念）。"""
    if app_type == MANAGED_AGENT_APP_TYPE:
        from bisheng.managed_agent.domain.models.dao import ManagedAgentDao
        from bisheng.managed_agent.domain.services.agent_card import parse_agent_card

        agent = await ManagedAgentDao.aget(int(app_id))
        if agent is None:
            return
        if agent.source_kind == "native":
            # Reuse the native shape check (workflow) or pass (assistant).
            if agent.native_app_type == FlowType.WORKFLOW.value:
                await assert_app_schedulable(str(agent.native_app_id), FlowType.WORKFLOW.value)
            return
        card = parse_agent_card(agent.agent_card)
        if any(item.file for item in card.inputs):
            raise ScheduledTaskAppNotSchedulableError(exception="file input is not supported on schedules")
        return

    if app_type != FlowType.WORKFLOW.value:
        return
    flow = await FlowDao.aget_flow_by_id(str(app_id))
    if flow is None:
        # 存在性由创建期绑定校验负责；这里只判形状。
        return
    reason = describe_unschedulable((flow.data if flow else None) or {})
    if reason:
        logger.info("scheduled_task: app {} is not schedulable: {}", app_id, reason)
        raise ScheduledTaskAppNotSchedulableError(exception=reason)
