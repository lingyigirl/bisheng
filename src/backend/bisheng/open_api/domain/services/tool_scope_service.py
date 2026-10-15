"""F076 `tools[]` 的范围解析与校验(T-18,spec AC-29/AC-30,INV-24)。

**白名单语义**:调用方列出即"本轮启用",未列出的一律不启用;不传则完全沿用
应用默认配置(与升级前一致)。任何未绑定 / 未启用 / 不可识别的工具都**显式报错**
(11815,`data.tool_key` 回带),绝不静默丢弃 —— 助手执行链里的
`skip_unauthorized=True`(`api/services/assistant_agent.py` 的 `init_tools`)
会静默吞掉无权限工具,所以校验必须发生在**端点层、进入执行链之前**。

两侧的"已绑定且启用"判据:

- 助手:现成的 `AssistantService.get_assistant_info(...).tool_list`
  (`GptsToolsRead`,取自 `assistantlink` 且只含未软删的工具行)。
- 工作流:`workflow.data.nodes[].data.group_params[].params[]` 中
  `key == 'tool_list'` 的条目(agent 节点的 LLM 自选工具),形态
  `{"key": <GptsTools.id>, "label": <名称>, "tool_key": <key>}`。
"""

from __future__ import annotations

from typing import Any

from bisheng.common.errcode.open_api import OpenApiToolNotAllowedError
from bisheng.open_api.domain.schemas.tools import OpenApiToolPayload

#: agent 节点工具参数在 group_params 里的 key。
AGENT_TOOL_LIST_KEY = "tool_list"
#: 工作流节点数据里 agent 类型节点的 type 值(workflow/common/node.py NodeType.AGENT)。
AGENT_NODE_TYPE = "agent"


def _raise_not_allowed(item: OpenApiToolPayload) -> None:
    raise OpenApiToolNotAllowedError(**{"tool_key": item.tool_key if item.tool_key else item.id})


# ---------------------------------------------------------------------------
# 助手
# ---------------------------------------------------------------------------


def resolve_assistant_tool_ids(assistant_info: Any, requested: list[OpenApiToolPayload] | None) -> list[int] | None:
    """校验助手侧工具选择 → 允许启用的 `GptsTools.id` 列表。

    `requested is None`(未携带)返回 `None` —— 调用方据此保持应用默认配置。
    匹配优先 `tool_key`,其次 `id`(两者都在"已绑定且启用"清单内才算命中)。
    """
    if requested is None:
        return None

    bound = list(getattr(assistant_info, "tool_list", None) or [])
    by_key = {t.tool_key: t.id for t in bound}
    by_id = {t.id: t.id for t in bound}

    selected: list[int] = []
    for item in requested:
        tool_id = None
        if item.tool_key:
            tool_id = by_key.get(item.tool_key)
        if tool_id is None and item.id:
            tool_id = by_id.get(item.id)
        if tool_id is None:
            _raise_not_allowed(item)
        if tool_id not in selected:
            selected.append(tool_id)
    return selected


# ---------------------------------------------------------------------------
# 工作流
# ---------------------------------------------------------------------------


def _iter_agent_tool_lists(workflow_data: dict | None):
    """产出 (节点 id, tool_list 条目列表) 对;只取 agent 节点的该参数。"""
    for node in (workflow_data or {}).get("nodes", []) or []:
        node_data = node.get("data") or {}
        if node_data.get("type") != AGENT_NODE_TYPE:
            continue
        for group_param in node_data.get("group_params", []) or []:
            for param in group_param.get("params", []) or []:
                if param.get("key") == AGENT_TOOL_LIST_KEY:
                    yield node.get("id"), list(param.get("value") or [])


def resolve_workflow_tool_override(
    workflow_data: dict | None, requested: list[OpenApiToolPayload] | None
) -> dict | None:
    """校验工作流侧工具选择 → 注入用的 override 片段。

    返回 `{"<agent 节点 id>": {"tool_list": [原条目…]}}`:
    每个 agent 节点被覆写为"请求项 ∩ 该节点已绑定工具";请求项未落在任何
    agent 节点的绑定清单里 → 11815。`requested is None` → 返回 `None`。
    """
    if requested is None:
        return None

    per_node: dict[str, dict[str, dict]] = {}
    for node_id, entries in _iter_agent_tool_lists(workflow_data):
        bucket = per_node.setdefault(node_id, {})
        for entry in entries:
            key = str(entry.get("key"))
            bucket[key] = entry
            if entry.get("tool_key"):
                bucket.setdefault(f"k:{entry['tool_key']}", entry)

    # 请求项必须能在某个 agent 节点里找到(按 id 优先,其次 tool_key)
    resolved: dict[str, list[dict]] = {}
    for item in requested:
        matched_any = False
        for node_id, bucket in per_node.items():
            entry = None
            if item.id:
                entry = bucket.get(str(item.id))
            if entry is None and item.tool_key:
                entry = bucket.get(f"k:{item.tool_key}")
            if entry is None:
                continue
            matched_any = True
            resolved.setdefault(node_id, [])
            if entry not in resolved[node_id]:
                resolved[node_id].append(entry)
        if not matched_any:
            _raise_not_allowed(item)

    # 白名单语义:凡是**能跑工具**的 agent 节点都要被覆写 —— 没被选中的节点
    # 覆写成空列表,避免"调用方只选了 A,节点 B 的工具却照跑"。
    override = {node_id: {AGENT_TOOL_LIST_KEY: resolved.get(node_id, [])} for node_id in per_node}
    return override or None


def assistant_tool_manifest(assistant_info: Any) -> list[dict]:
    """T-19:助手详情里返回的可用工具清单(已绑定且启用)。"""
    return [
        {"id": tool.id, "tool_key": tool.tool_key, "name": tool.name, "desc": tool.desc}
        for tool in (getattr(assistant_info, "tool_list", None) or [])
    ]


def workflow_tool_manifest(workflow_data: dict | None) -> list[dict]:
    """T-19:工作流详情里返回的可用工具清单(全部 agent 节点 tool_list 的并集)。"""
    seen: dict[str, dict] = {}
    for _node_id, entries in _iter_agent_tool_lists(workflow_data):
        for entry in entries:
            key = str(entry.get("key"))
            if key in seen:
                continue
            seen[key] = {
                "id": entry.get("key"),
                "tool_key": entry.get("tool_key"),
                "name": entry.get("label"),
                "desc": entry.get("desc"),
            }
    return list(seen.values())
