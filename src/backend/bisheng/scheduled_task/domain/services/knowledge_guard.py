"""F077 创建期知识可见性校验（spec AC-28/AC-29，INV-21 / INV-25 ④）。

**为什么只在创建期查**：定时运行的知识可见集合由应用既有的「用户知识库权限校验」
开关决定（关=配置者可见范围、开=运行身份可见范围）。开关为开的引用，如果所选运行
身份根本读不到，任务会"成功"但检索为空——那是最难排查的一类失败，所以在唯一能
拦住它的位置（创建任务时）拒绝（11913），并在错误里带回缺授权的范围。

运行期授权被撤**不回退**：ON/OFF 由开关值决定，与授权结果无关，代码里不存在
"判定失败后改用配置者范围"的路径（spec AC-29 的反向约束）。

**引用收集与运行期同源**：工作流引用来自 `flow.data` 的节点参数（与运行期
`workflow/common/knowledge.py` 读的是同一份 `knowledge` / `advanced_retrieval_switch`）；
助手引用来自 `AssistantLink.knowledge_id` + `assistant.knowledge_auth`（与
`api/services/assistant_agent.py:334-347` 同源）。文件类/多输入等限制不在这里。
"""

from __future__ import annotations

from dataclasses import dataclass

from loguru import logger

from bisheng.common.errcode.scheduled_task import ScheduledTaskKnowledgeUnauthorizedError
from bisheng.database.models.assistant import AssistantDao, AssistantLinkDao
from bisheng.database.models.flow import FlowDao, FlowType
from bisheng.knowledge.domain.models.knowledge import KnowledgeDao, KnowledgeTypeEnum
from bisheng.knowledge.domain.services.knowledge_space_service import KnowledgeSpaceService
from bisheng.managed_agent.domain.constants import MANAGED_AGENT_APP_TYPE
from bisheng.user.domain.models.user import UserDao


@dataclass(frozen=True)
class KnowledgeRef:
    """一个被应用绑定的知识范围（知识库或知识空间）。"""

    kind: str  # "knowledge" | "space"
    id: int
    user_auth: bool  # 该引用所属节点/应用的「用户知识库权限校验」开关


def collect_workflow_refs(data: dict) -> list[KnowledgeRef]:
    """从 ``flow.data`` 里收集知识引用与各自的 user_auth（**纯函数**，可直接单测）。

    ``flow.data`` 的节点参数形状：
    ``nodes[*].data.group_params[*].params[*]``，其中 ``key == "knowledge"`` 的
    ``value`` 是 ``{"type": "knowledge"|"space", "value": [{"key": <id>}, ...]}``；
    开关在新形态里是 ``advanced_retrieval_switch.user_auth``，旧形态在顶层
    ``user_auth``（两种都读，与运行期一致）。
    """
    refs: list[KnowledgeRef] = []
    for node in (data or {}).get("nodes") or []:
        params: dict = {}
        for group in (node.get("data") or {}).get("group_params") or []:
            for param in group.get("params") or []:
                params[param.get("key")] = param.get("value")

        knowledge = params.get("knowledge")
        if not isinstance(knowledge, dict):
            continue
        items = knowledge.get("value") or []
        if not items:
            continue

        advanced = params.get("advanced_retrieval_switch") or {}
        user_auth = bool(advanced.get("user_auth", params.get("user_auth", False)))
        raw_kind = str(knowledge.get("type") or "knowledge")
        kind = "space" if raw_kind in ("space", "knowledge_space") else "knowledge"

        for item in items:
            raw_id = item.get("key") if isinstance(item, dict) else item
            try:
                ref_id = int(raw_id)
            except (TypeError, ValueError):
                # 运行期会把无法解析的项当作"没有这个知识库"；创建期同样跳过而不是
                # 报配置错——那种错会在第一次运行时以空检索的形式暴露，已经有 AC-29 兜底。
                logger.warning("scheduled_task: ignoring unparsable knowledge ref {!r}", raw_id)
                continue
            refs.append(KnowledgeRef(kind=kind, id=ref_id, user_auth=user_auth))
    return refs


async def _collect_assistant_refs(assistant) -> list[KnowledgeRef]:
    links = await AssistantLinkDao.get_assistant_link(assistant.id)
    if not links:
        return []
    ids = [int(link.knowledge_id) for link in links if link.knowledge_id]
    if not ids:
        return []
    type_by_id = {row.id: row.type for row in KnowledgeDao.get_list_by_ids(ids)}
    refs = []
    for knowledge_id in ids:
        kind = "space" if type_by_id.get(knowledge_id) == KnowledgeTypeEnum.SPACE.value else "knowledge"
        refs.append(KnowledgeRef(kind=kind, id=knowledge_id, user_auth=bool(assistant.knowledge_auth)))
    return refs


async def collect_app_refs(app_id: str, app_type: int) -> list[KnowledgeRef]:
    """按应用类型收集知识引用。"""
    if app_type == MANAGED_AGENT_APP_TYPE:
        from bisheng.managed_agent.domain.models.dao import ManagedAgentDao

        agent = await ManagedAgentDao.aget(int(app_id))
        if agent is None or agent.source_kind != "native":
            # Third-party agents have no platform knowledge refs (INV-25 clause 4
            # only applies to native apps).
            return []
        return await collect_app_refs(str(agent.native_app_id), int(agent.native_app_type))
    if app_type == FlowType.WORKFLOW.value:
        flow = await FlowDao.aget_flow_by_id(str(app_id))
        return collect_workflow_refs((flow.data if flow else None) or {})
    if app_type == FlowType.ASSISTANT.value:
        assistant = await AssistantDao.aget_one_assistant(str(app_id))
        if assistant is None:
            return []
        return await _collect_assistant_refs(assistant)
    return []


async def assert_app_knowledge_readable(*, app_id: str, app_type: int, owner_user_id: int, tenant_id: int) -> None:
    """开关为开的引用必须对运行身份可读；否则 11913（回带缺授权的范围）。"""
    refs = await collect_app_refs(app_id, app_type)
    guarded = [ref for ref in refs if ref.user_auth]
    if not guarded:
        return

    missing: list[int] = []

    kb_ids = [ref.id for ref in guarded if ref.kind == "knowledge"]
    if kb_ids:
        user = await UserDao.aget_user(owner_user_id)
        readable_ids: set[int] = set()
        if user is not None:
            readable = KnowledgeDao.judge_knowledge_permission(user_name=user.user_name, knowledge_ids=kb_ids)
            readable_ids = {int(row.id) for row in readable}
        missing.extend(kb_id for kb_id in kb_ids if kb_id not in readable_ids)

    space_ids = [ref.id for ref in guarded if ref.kind == "space"]
    for space_id in space_ids:
        if not await KnowledgeSpaceService._user_can_read_space(owner_user_id, space_id):
            missing.append(space_id)

    if missing:
        raise ScheduledTaskKnowledgeUnauthorizedError(
            exception="run identity lacks read access to bound knowledge",
            knowledge_ids=sorted(set(missing)),
        )
