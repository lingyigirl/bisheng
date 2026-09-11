"""F056 — agent_flow node (智能体节点): synchronous inline invocation of a
published WORKFLOW flow as a sub-flow.

Covers the node's _run algorithm (validation, cycle guard, input injection,
output extraction), the ContextVar ancestor chain and parse_log — MagicMock
style, no DB (mirrors test/workflow/test_rag_utils.py).
"""

from unittest.mock import MagicMock, patch

import pytest

from bisheng.workflow.callback.base_callback import BaseCallback
from bisheng.workflow.common.node import BaseNodeData, NodeGroupParams, NodeParams, NodeType
from bisheng.workflow.common.workflow import WorkflowStatus
from bisheng.workflow.graph.graph_state import GraphState
from bisheng.workflow.nodes.agent_flow.agent_flow import (
    AgentFlowNode,
    agent_flow_ancestors,
)
from bisheng.workflow.nodes.node_manage import NodeFactory

INPUT = WorkflowStatus.INPUT.value
SUCCESS = WorkflowStatus.SUCCESS.value
FAILED = WorkflowStatus.FAILED.value

CONFIG = {
    "flow_id": "callee_flow",
    "input_mapping": [{"field": "question", "mode": "var", "value": "input_1.user_input"}],
    "outputs": [{"key": "output", "label": "输出", "ref": "llm_1.output"}],
}


def _node_data(config: dict | None = None, **extra) -> BaseNodeData:
    config = config if config is not None else CONFIG
    return BaseNodeData(
        id="agent_flow_1",
        type=NodeType.AGENT_FLOW.value,
        name="智能体",
        v=1,
        group_params=[
            NodeGroupParams(
                name="智能体配置",
                params=[
                    NodeParams(
                        key="agent_flow_config",
                        label="选择智能体",
                        type="agent_flow_config",
                        value=config,
                    )
                ],
            )
        ],
        **extra,
    )


def _make_node(config: dict | None = None, workflow_id: str = "parent_flow", user_id: int = 7) -> AgentFlowNode:
    return NodeFactory.instance_node(
        node_type=NodeType.AGENT_FLOW.value,
        node_data=_node_data(config),
        user_id=user_id,
        workflow_id=workflow_id,
        workflow_name="parent",
        graph_state=GraphState(),
        target_edges=[],
        max_steps=10,
        callback=BaseCallback(),
    )


def _callee_flow() -> MagicMock:
    flow = MagicMock()
    flow.name = "客服问答"
    flow.flow_type = 10  # WORKFLOW
    flow.status = 2  # ONLINE
    flow.data = {"nodes": [{"data": {"id": "input_1", "type": "input"}}], "edges": []}
    flow.tenant_id = 1
    flow.user_id = 2
    return flow


def _child_workflow(statuses: list, nodes_map: dict | None = None, pool: dict | None = None) -> MagicMock:
    child = MagicMock()
    child.run = MagicMock(side_effect=list(statuses))  # each item: (status, reason)
    child.graph_engine.nodes_map = nodes_map or {}
    child.graph_engine.graph_state.get_variable_by_str = MagicMock(side_effect=lambda ref: (pool or {}).get(ref))
    return child


def _patch_base(flow: MagicMock, child: MagicMock, permission: bool = True):
    from contextlib import ExitStack

    stack = ExitStack()
    stack.enter_context(patch("bisheng.workflow.nodes.agent_flow.agent_flow.FlowDao.get_flow_by_id", return_value=flow))
    stack.enter_context(
        patch(
            "bisheng.workflow.nodes.agent_flow.agent_flow.ApplicationPermissionService.has_any_permission_sync",
            return_value=permission,
        )
    )
    stack.enter_context(
        patch("bisheng.workflow.nodes.agent_flow.agent_flow.LoginUser.init_login_user_sync", return_value=MagicMock())
    )
    conf = MagicMock()
    conf.max_steps = 10
    conf.timeout = 30
    # settings is a pydantic model — patch the CLASS method, not the instance
    stack.enter_context(
        patch("bisheng.common.services.config_service.ConfigService.get_workflow_conf", return_value=conf)
    )
    stack.enter_context(patch("bisheng.workflow.graph.workflow.Workflow", return_value=child))
    return stack


# --- happy path -----------------------------------------------------------------


def test_run_extracts_selected_outputs():
    """Success: child runs to SUCCESS, selected refs are extracted as output_<key>."""
    flow = _callee_flow()
    child = _child_workflow([(SUCCESS, "")], pool={"llm_1.output": "答案"})
    node = _make_node()
    with _patch_base(flow, child):
        result = node._run("exec_1")

    assert result == {"output_output": "答案"}
    assert child.run.call_count == 1


def test_child_constructed_with_callee_tenant_and_creator():
    """The child Workflow receives the CALLEE's tenant_id/flow_user_id and
    skip_input_interrupt=True (nested-stream checkpoint fix)."""
    flow = _callee_flow()
    child = _child_workflow([(SUCCESS, "")])
    node = _make_node()
    kwargs_box: dict = {}

    def _factory(*args, **kwargs):
        kwargs_box.update(kwargs)
        return child

    with _patch_base(flow, child):
        with patch("bisheng.workflow.graph.workflow.Workflow", side_effect=_factory):
            node._run("exec_1")

    assert kwargs_box["tenant_id"] == 1
    assert kwargs_box["flow_user_id"] == 2
    assert kwargs_box["skip_input_interrupt"] is True


def test_input_injection_round():
    """Mapped fields are pre-fed via handle_input and the child runs to SUCCESS
    in a single pass (skip_input_interrupt — F057 nested-checkpoint fix)."""
    flow = _callee_flow()
    input_node = MagicMock()
    input_node.id = "input_1"
    input_node.type = NodeType.INPUT.value
    input_node.node_data = BaseNodeData(
        id="input_1",
        type=NodeType.INPUT.value,
        tab={"value": "form_input"},
        group_params=[
            NodeGroupParams(
                name="输入",
                params=[
                    NodeParams(
                        key="form_input",
                        type="form",
                        value=[
                            {"key": "question", "type": "text", "value": "", "label": "问题"},
                            {"key": "attach", "type": "file", "value": "", "label": "附件"},
                        ],
                    )
                ],
            )
        ],
    )
    child = _child_workflow([(SUCCESS, "")], nodes_map={"input_1": input_node})
    node = _make_node()
    node.graph_state.set_variable_by_str("input_1.user_input", "上游问题")
    with _patch_base(flow, child):
        result = node._run("exec_1")

    assert result == {"output_output": None}
    assert child.run.call_count == 1
    # 未映射的 file 字段不注入；子流程用 skip_input_interrupt 单阶段跑完
    input_node.handle_input.assert_called_once_with({"question": "上游问题"})


def test_input_injection_includes_mapped_file_fields():
    """v2: file fields are mappable — a var-mode mapping injects the file URLs
    under the callee's static form item key (runtime translation happens in
    InputNode.handle_input)."""
    flow = _callee_flow()
    input_node = MagicMock()
    input_node.id = "input_1"
    input_node.type = NodeType.INPUT.value
    input_node.node_data = BaseNodeData(
        id="input_1",
        type=NodeType.INPUT.value,
        tab={"value": "form_input"},
        group_params=[
            NodeGroupParams(
                name="输入",
                params=[
                    NodeParams(
                        key="form_input",
                        type="form",
                        value=[
                            {"key": "question", "type": "text", "value": "", "label": "问题"},
                            {"key": "attach", "type": "file", "value": "", "label": "附件"},
                        ],
                    )
                ],
            )
        ],
    )
    config = {
        **CONFIG,
        "input_mapping": [
            {"field": "question", "mode": "var", "value": "input_1.user_input"},
            {"field": "attach", "mode": "var", "value": "input_1.attach_paths"},
        ],
    }
    child = _child_workflow([(SUCCESS, "")], nodes_map={"input_1": input_node})
    node = _make_node(config)
    node.graph_state.set_variable_by_str("input_1.user_input", "上游问题")
    node.graph_state.set_variable_by_str("input_1.attach_paths", ["https://minio/f1.pdf"])
    with _patch_base(flow, child):
        node._run("exec_1")

    assert child.run.call_count == 1
    input_node.handle_input.assert_called_once_with(
        {"question": "上游问题", "attach": ["https://minio/f1.pdf"]}
    )


def test_unmapped_fields_keep_callee_defaults():
    """Empty mapping → input node pre-fed with no keys (callee defaults apply)."""
    flow = _callee_flow()
    input_node = MagicMock()
    input_node.id = "input_1"
    input_node.type = NodeType.INPUT.value
    input_node.node_data = BaseNodeData(
        id="input_1", type=NodeType.INPUT.value, tab={"value": "form_input"}, group_params=[]
    )
    child = _child_workflow([(SUCCESS, "")], nodes_map={"input_1": input_node})
    node = _make_node({**CONFIG, "input_mapping": []})
    with _patch_base(flow, child):
        node._run("exec_1")

    assert child.run.call_count == 1
    input_node.handle_input.assert_called_once_with({})


def test_no_input_flow_runs_once():
    """A callee without an input node (pure preset flow) completes in one round."""
    flow = _callee_flow()
    child = _child_workflow([(SUCCESS, "")])
    node = _make_node()
    with _patch_base(flow, child):
        result = node._run("exec_1")

    assert result == {"output_output": None}
    assert child.run.call_count == 1


# --- failure paths -------------------------------------------------------------


def test_interactive_input_rejected():
    """A callee that still pauses at INPUT (e.g. an interactive output node)
    is rejected — agent_flow cannot do multi-round interaction."""
    flow = _callee_flow()
    input_node = MagicMock()
    input_node.id = "input_1"
    input_node.type = NodeType.INPUT.value
    input_node.node_data = BaseNodeData(id="input_1", type=NodeType.INPUT.value)
    child = _child_workflow([(INPUT, "")], nodes_map={"input_1": input_node})
    node = _make_node()
    with _patch_base(flow, child):
        with pytest.raises(Exception, match="交互式输入"):
            node._run("exec_1")


def test_interactive_output_without_input_node_rejected():
    flow = _callee_flow()
    child = _child_workflow([(INPUT, "")], nodes_map={})
    node = _make_node()
    with _patch_base(flow, child):
        with pytest.raises(Exception, match="交互式输入"):
            node._run("exec_1")


def test_child_failed_carries_reason():
    flow = _callee_flow()
    child = _child_workflow([(FAILED, "子流程炸了")])
    node = _make_node()
    with _patch_base(flow, child):
        with pytest.raises(Exception, match="子流程炸了"):
            node._run("exec_1")


def test_no_target_configured():
    node = _make_node({"flow_id": "", "input_mapping": [], "outputs": []})
    with pytest.raises(Exception, match="未配置"):
        node._run("exec_1")


def test_self_call_rejected():
    """flow_id == own workflow_id → circular error before any DB access."""
    node = _make_node({**CONFIG, "flow_id": "parent_flow"})
    with pytest.raises(Exception, match="循环"):
        node._run("exec_1")


def test_ancestor_chain_rejected_and_reset():
    """A callee already on the chain (A→B→A) is rejected; ContextVar restored."""
    flow = _callee_flow()
    child = _child_workflow([(SUCCESS, "")])
    node = _make_node({**CONFIG, "flow_id": "ancestor_flow"})
    token = agent_flow_ancestors.set(("ancestor_flow", "root_flow"))
    try:
        with _patch_base(flow, child):
            with pytest.raises(Exception, match="循环"):
                node._run("exec_1")
    finally:
        agent_flow_ancestors.reset(token)
    assert agent_flow_ancestors.get() == ()


def test_nested_chain_adds_own_workflow_id():
    """During the child's RUN the ancestor chain contains the parent id."""
    flow = _callee_flow()
    child = _child_workflow([(SUCCESS, "")])
    node = _make_node()
    seen: list = []

    def _watch_run(*a, **kw):
        seen.append(agent_flow_ancestors.get())
        return (SUCCESS, "")

    child.run = MagicMock(side_effect=_watch_run)
    with _patch_base(flow, child):
        node._run("exec_1")
    assert ("parent_flow",) in seen
    assert agent_flow_ancestors.get() == ()


def test_callee_not_found():
    child = _child_workflow([(SUCCESS, "")])
    node = _make_node()
    with (
        patch("bisheng.workflow.nodes.agent_flow.agent_flow.FlowDao.get_flow_by_id", return_value=None),
        patch("bisheng.workflow.graph.workflow.Workflow", return_value=child),
    ):
        with pytest.raises(Exception, match="不存在"):
            node._run("exec_1")


def test_callee_wrong_type_rejected():
    flow = _callee_flow()
    flow.flow_type = 5  # ASSISTANT
    child = _child_workflow([(SUCCESS, "")])
    node = _make_node()
    with _patch_base(flow, child):
        with pytest.raises(Exception, match="工作流类型"):
            node._run("exec_1")


def test_callee_not_published_rejected():
    flow = _callee_flow()
    flow.status = 1  # OFFLINE
    child = _child_workflow([(SUCCESS, "")])
    node = _make_node()
    with _patch_base(flow, child):
        with pytest.raises(Exception, match="未发布"):
            node._run("exec_1")


def test_callee_empty_graph_rejected():
    flow = _callee_flow()
    flow.data = None
    child = _child_workflow([(SUCCESS, "")])
    node = _make_node()
    with _patch_base(flow, child):
        with pytest.raises(Exception, match="为空"):
            node._run("exec_1")


def test_permission_denied_rejected():
    flow = _callee_flow()
    child = _child_workflow([(SUCCESS, "")])
    node = _make_node()
    with _patch_base(flow, child, permission=False):
        with pytest.raises(Exception, match="无权"):
            node._run("exec_1")


def test_parse_log_shape():
    node = _make_node()
    log = node.parse_log("exec_1", {"output_output": "答案"})
    assert log == [[{"key": "output_output", "value": "答案", "type": "variable"}]]
