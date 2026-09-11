"""智能体节点 (F056) — synchronously run another published WORKFLOW flow inline as a sub-flow.

Design notes:
- The child runs in the SAME thread (workflow nodes execute in Celery
  ``-P threads``), so a module-level ContextVar is sufficient for cycle
  detection. If the child is ever moved to a detached task, this mechanism
  must be replaced (ContextVars do not propagate across threads).
- Child events go to a minimal callback: sub-flow streaming/logs are
  intentionally dropped in v1 — only the extracted outputs surface in the
  parent via parse_log.
- ``__init__`` must stay DB-free: publishing a flow instantiates every node
  (workflow validation), so no FlowDao/LLM access is allowed here.
- KNOWN LIMITATION: single-node test-run (``run_once``) executes in the API
  process. Callees whose nodes hop to the process async bridge (tool nodes,
  etc.) can raise cross-event-loop errors there and poison the API process —
  production runs in the workflow worker are unaffected (same loop topology
  as the parent's own nodes). Verified 2026-09-07: a pure llm/code callee
  runs fine under run_once; a tool-bearing callee fails with "Future attached
  to a different loop".

"""

from contextvars import ContextVar
from typing import Any

from bisheng.common.services.config_service import settings
from bisheng.database.models.flow import FlowDao, FlowStatus, FlowType
from bisheng.permission.domain.services.application_permission_service import ApplicationPermissionService
from bisheng.user.domain.services.auth import LoginUser
from bisheng.workflow.callback.base_callback import BaseCallback
from bisheng.workflow.callback.event import UserInputData
from bisheng.workflow.common.node import NodeType
from bisheng.workflow.common.workflow import WorkflowStatus
from bisheng.workflow.nodes.base import BaseNode

# NOTE: Workflow (bisheng.workflow.graph.workflow) is imported inside _run —
# a top-level import would create a cycle: node_manage → agent_flow →
# graph.workflow → graph_engine → node_manage.

# Chain of parent workflow_ids currently being invoked in this thread.
agent_flow_ancestors: ContextVar[tuple[str, ...]] = ContextVar("agent_flow_ancestors", default=())


class AgentFlowCallback(BaseCallback):
    """Minimal callback for the child run — records the interrupting input node
    and drops everything else (v1: no sub-flow streaming to the parent)."""

    def __init__(self):
        super().__init__()
        self.interrupt: UserInputData | None = None

    def on_user_input(self, data: UserInputData):
        self.interrupt = data


class AgentFlowNode(BaseNode):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        config = self.node_params.get("agent_flow_config") or {}
        self._flow_id = config.get("flow_id") or ""
        self._input_mapping: list[dict] = config.get("input_mapping") or []
        self._outputs: list[dict] = config.get("outputs") or []

    @staticmethod
    def _input_node_field_keys(node_instance: BaseNode) -> set[str]:
        """Field keys accepted by one input node of the callee.

        v2: file fields are mappable too — input_mapping addresses them by their
        static form item keys, and the callee's InputNode.handle_input translates
        them to the per-run file_<uuid8> runtime keys.
        """
        data = node_instance.node_data
        tab = (data.tab or {}).get("value") if data.tab else None
        if tab == "dialog_input":
            return {"user_input"}
        keys: set[str] = set()
        for group in data.group_params or []:
            for param in group.params:
                if param.key == "form_input" and isinstance(param.value, list):
                    keys |= {f.get("key") for f in param.value}
        return keys

    def _resolve_input_map(self) -> dict[str, Any]:
        input_map: dict[str, Any] = {}
        for m in self._input_mapping:
            field, mode, value = m.get("field"), m.get("mode"), m.get("value")
            if not field or value in (None, ""):
                continue
            if mode == "var":
                input_map[field] = self.get_other_node_variable(value)
            else:
                input_map[field] = value
        return input_map

    def _run(self, unique_id: str) -> dict[str, Any]:
        flow_id = self._flow_id
        if not flow_id:
            raise Exception(
                "智能体节点未配置要调用的流程 / agent flow node has no target flow configured"
            )

        # 1. Self / cycle guard — the ancestor chain lives in this thread's ContextVar
        ancestors = agent_flow_ancestors.get()
        if flow_id == self.workflow_id or flow_id in ancestors:
            raise Exception(
                f"检测到智能体流程循环调用: {self.name} 引用的流程处于当前调用链中 "
                f"/ circular agent-flow invocation detected on {flow_id}"
            )

        # 2. Fetch + validate the callee
        flow_info = FlowDao.get_flow_by_id(flow_id)
        if not flow_info:
            raise Exception(f"智能体引用的流程不存在: {flow_id} / agent flow {flow_id} not found")
        if flow_info.flow_type != FlowType.WORKFLOW.value:
            raise Exception(
                f"智能体只能引用工作流类型的应用: {flow_info.name} / only WORKFLOW flows are supported"
            )
        if flow_info.status != FlowStatus.ONLINE.value:
            raise Exception(f"智能体引用的流程未发布: {flow_info.name} / agent flow is not published")
        if not flow_info.data or not flow_info.data.get("nodes"):
            raise Exception(f"智能体引用的流程为空: {flow_info.name} / agent flow has no graph data")

        # 3. Permission for the RUNTIME user (self.user_id)
        login_user = LoginUser.init_login_user_sync(user_id=self.user_id, user_name="")
        if not ApplicationPermissionService.has_any_permission_sync(
            login_user, "workflow", str(flow_id), ["use_app"]
        ):
            raise Exception(
                f"当前用户无权使用该智能体流程: {flow_info.name} / permission denied for agent flow"
            )

        # 4. Resolve mapped inputs from parent variables
        input_map = self._resolve_input_map()

        # 5. Build the child workflow — mirror tasks.py threading (F022/F041 invariants)
        from bisheng.workflow.graph.workflow import Workflow

        callback = AgentFlowCallback()
        workflow_conf = settings.get_workflow_conf()
        child = Workflow(
            flow_id,
            flow_info.name,
            self.user_id,
            flow_info.data,
            False,
            workflow_conf.max_steps,
            workflow_conf.timeout,
            callback,
            tenant_id=getattr(flow_info, "tenant_id", None),
            flow_user_id=getattr(flow_info, "user_id", None),
            skip_input_interrupt=True,
        )

        # 6. Pre-feed every input node and run to completion in ONE pass — an
        #    interrupt at INPUT inside a NESTED graph stream (this node runs
        #    inside the parent's langgraph stream) aborts the inner checkpoint
        #    and breaks the resume round (same fix as the F057 loop node).
        token = agent_flow_ancestors.set((*ancestors, self.workflow_id))
        try:
            input_nodes = [
                n for n in child.graph_engine.nodes_map.values() if n.type == NodeType.INPUT.value
            ]
            for n in input_nodes:
                n.handle_input(
                    {k: v for k, v in input_map.items() if k in self._input_node_field_keys(n)}
                )
            status, reason = child.run()
        finally:
            agent_flow_ancestors.reset(token)

        if status == WorkflowStatus.INPUT.value:
            raise Exception(
                f"智能体子流程需要交互式输入, 暂不支持: {flow_info.name} "
                "/ interactive input in agent flow is not supported"
            )
        if status != WorkflowStatus.SUCCESS.value:
            raise Exception(
                f"智能体子流程执行失败: {reason or 'unknown'} / agent flow execution failed: {reason or 'unknown'}"
            )

        # 7. Extract the selected outputs. Keys carry the `output_` prefix to
        #    match the frontend handleChangeOutPut linkage convention.
        pool = child.graph_engine.graph_state
        result: dict[str, Any] = {}
        for out in self._outputs:
            ref, key = out.get("ref"), out.get("key")
            if not ref or not key:
                continue
            result[f"output_{key}"] = pool.get_variable_by_str(ref)
        return result

    def parse_log(self, unique_id: str, result: dict) -> Any:
        return [[{"key": k, "value": v, "type": "variable"} for k, v in result.items()]]
