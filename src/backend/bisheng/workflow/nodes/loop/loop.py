"""循环节点 (F057) — iterate a list variable, running a published WORKFLOW sub-flow per element.

Design notes (mirroring the agent_flow node, F056):
- The child runs in the SAME thread (workflow nodes execute in Celery
  ``-P threads``), so the shared module-level ContextVar
  ``agent_flow_ancestors`` is sufficient for cycle detection across
  loop/agent_flow chains. If the child is ever moved to a detached task,
  this mechanism must be replaced (ContextVars do not propagate across
  threads).
- A fresh ``Workflow`` is built per iteration: the child graph is stateful
  (MemorySaver checkpoint, fixed thread_id), so re-running one instance is
  unsafe.
- Child events go to a minimal callback: sub-flow streaming/logs are
  intentionally dropped in v1 — only the extracted outputs surface in the
  parent via parse_log.
- ``__init__`` must stay DB-free: publishing a flow instantiates every node
  (workflow validation), so no FlowDao/LLM access is allowed here.
- KNOWN LIMITATION: single-node test-run (``run_once``) executes in the API
  process. Callees whose nodes hop to the process async bridge (tool nodes,
  etc.) can raise cross-event-loop errors there and poison the API process —
  production runs in the workflow worker are unaffected (same loop topology
  as the parent's own nodes).
"""

from typing import Any

from loguru import logger

from bisheng.common.services.config_service import settings
from bisheng.database.models.flow import FlowDao, FlowStatus, FlowType
from bisheng.permission.domain.services.application_permission_service import ApplicationPermissionService
from bisheng.user.domain.services.auth import LoginUser
from bisheng.workflow.common.node import NodeType
from bisheng.workflow.common.workflow import WorkflowStatus
from bisheng.workflow.nodes.agent_flow.agent_flow import (
    AgentFlowCallback,
    AgentFlowNode,
    agent_flow_ancestors,
)
from bisheng.workflow.nodes.base import BaseNode

# NOTE: Workflow (bisheng.workflow.graph.workflow) is imported inside _run —
# a top-level import would create a cycle: node_manage → loop →
# graph.workflow → graph_engine → node_manage.


class LoopNode(BaseNode):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        config = self.node_params.get("loop_config") or {}
        self._loop_var = config.get("loop_var") or ""
        self._item_var = config.get("item_var") or "item"
        self._flow_id = config.get("flow_id") or ""
        self._input_mapping: list[dict] = config.get("input_mapping") or []
        self._outputs: list[dict] = config.get("outputs") or []
        try:
            self._max_iterations = int(config.get("max_iterations") or 100)
        except (TypeError, ValueError):
            self._max_iterations = 100

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

    @staticmethod
    def _static_form_check(flow_data: dict, mapped_fields: set[str]) -> None:
        """The loop body must not require interactive user input.

        Rejects dialog-input INPUT nodes outright; form-input fields (except
        file fields, which are regenerated per invocation) must all be covered
        by the input_mapping — the mapping is the only delivery channel for
        the current item.
        """
        for node in flow_data.get("nodes") or []:
            data = node.get("data") or {}
            if data.get("type") != "input":
                continue
            tab = (data.get("tab") or {}).get("value")
            if tab == "dialog_input":
                raise Exception(
                    "循环体子流程不能包含对话输入: {} / loop body sub-flow cannot contain dialog input".format(
                        data.get("name") or node.get("id")
                    )
                )
            form_fields = []
            for group in data.get("group_params") or []:
                for param in group.get("params") or []:
                    if param.get("key") == "form_input" and isinstance(param.get("value"), list):
                        form_fields.extend(
                            f.get("key")
                            for f in param["value"]
                            if f.get("type") != "file" and f.get("key")
                        )
            unmapped = [f for f in form_fields if f not in mapped_fields]
            if unmapped:
                raise Exception(
                    "循环体子流程存在未映射的表单字段: {} / loop body sub-flow has unmapped form fields — "
                    "sub-flow must not require form input".format("、".join(unmapped))
                )

    def _run(self, unique_id: str) -> dict[str, Any]:
        if not self._loop_var:
            raise Exception("循环节点未配置循环变量 / loop node has no loop variable configured")
        if not self._flow_id:
            raise Exception("循环节点未配置要调用的子流程 / loop node has no target flow configured")
        if "." in self._item_var or "#" in self._item_var:
            raise Exception(
                "循环项变量名不能包含 . 或 # / item variable name must not contain . or #"
            )

        # 1. Self / cycle guard — the ancestor chain lives in this thread's ContextVar
        ancestors = agent_flow_ancestors.get()
        if self._flow_id == self.workflow_id or self._flow_id in ancestors:
            raise Exception(
                f"检测到循环调用: {self.name} 引用的流程处于当前调用链中 "
                f"/ circular loop invocation detected on {self._flow_id}"
            )

        # 2. Fetch + validate the callee
        flow_info = FlowDao.get_flow_by_id(self._flow_id)
        if not flow_info:
            raise Exception(f"循环体引用的流程不存在: {self._flow_id} / loop flow {self._flow_id} not found")
        if flow_info.flow_type != FlowType.WORKFLOW.value:
            raise Exception(
                f"循环节点只能引用工作流类型的应用: {flow_info.name} / only WORKFLOW flows are supported"
            )
        if flow_info.status != FlowStatus.ONLINE.value:
            raise Exception(f"循环体引用的流程未发布: {flow_info.name} / loop flow is not published")
        if not flow_info.data or not flow_info.data.get("nodes"):
            raise Exception(f"循环体引用的流程为空: {flow_info.name} / loop flow has no graph data")

        # 3. Permission for the RUNTIME user (self.user_id)
        login_user = LoginUser.init_login_user_sync(user_id=self.user_id, user_name="")
        if not ApplicationPermissionService.has_any_permission_sync(
            login_user, "workflow", str(self._flow_id), ["use_app"]
        ):
            raise Exception(
                f"当前用户无权使用该循环体流程: {flow_info.name} / permission denied for loop flow"
            )

        # 4. The loop body must not require interactive input
        mapped_fields = {m.get("field") for m in self._input_mapping if m.get("field")}
        self._static_form_check(flow_info.data, mapped_fields)

        # 5. Resolve the list to iterate
        loop_values = self.get_other_node_variable(self._loop_var)
        if not isinstance(loop_values, (list, tuple)):
            raise Exception("循环变量不是列表 / loop variable is not a list")

        items = list(loop_values)
        if len(items) > self._max_iterations:
            logger.warning(
                "loop node {} truncated {} items to max_iterations {}",
                self.id,
                len(items),
                self._max_iterations,
            )
            items = items[: self._max_iterations]

        outputs_acc = {
            out["key"]: []
            for out in self._outputs
            if out.get("key") and out.get("ref")
        }

        # 6. Iterate: inject the current item, run a fresh child, collect outputs
        from bisheng.workflow.graph.workflow import Workflow

        workflow_conf = settings.get_workflow_conf()
        token = agent_flow_ancestors.set((*ancestors, self.workflow_id))
        try:
            for item in items:
                # expose the current item to the parent state so var-mode
                # mappings can reference "<loop_id>.<item_var>"
                self.graph_state.set_variable(self.id, self._item_var, item)
                input_map = self._resolve_input_map()

                callback = AgentFlowCallback()
                child = Workflow(
                    self._flow_id,
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

                # Pre-feed every input node so the graph runs to completion in
                # one pass: an interrupt at INPUT inside a NESTED graph stream
                # aborts the inner checkpoint and breaks the resume round.
                input_nodes = [
                    n
                    for n in child.graph_engine.nodes_map.values()
                    if n.type == NodeType.INPUT.value
                ]
                for n in input_nodes:
                    n.handle_input(
                        {
                            k: v
                            for k, v in input_map.items()
                            if k in AgentFlowNode._input_node_field_keys(n)
                        }
                    )

                status, reason = child.run()
                if status == WorkflowStatus.INPUT.value:
                    raise Exception(
                        f"循环体子流程需要交互式输入, 暂不支持: {flow_info.name} "
                        "/ interactive input in loop flow is not supported"
                    )
                if status != WorkflowStatus.SUCCESS.value:
                    raise Exception(
                        f"循环体子流程执行失败: {reason or 'unknown'} "
                        f"/ loop body sub-flow failed: {reason or 'unknown'}"
                    )

                pool = child.graph_engine.graph_state
                for out in self._outputs:
                    key, ref = out.get("key"), out.get("ref")
                    if not key or not ref:
                        continue
                    outputs_acc[key].append(pool.get_variable_by_str(ref))
        finally:
            agent_flow_ancestors.reset(token)

        return outputs_acc

    def parse_log(self, unique_id: str, result: dict) -> Any:
        return [[{"key": k, "value": v, "type": "variable"} for k, v in result.items()]]
