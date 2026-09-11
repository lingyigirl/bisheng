"""F057 — loop node (循环节点): iterate a list variable, running a published
WORKFLOW sub-flow per element and collecting per-iteration outputs into lists.

Covers the node's _run algorithm (validation, cycle guard, item injection,
output collection), the shared ContextVar ancestor chain and parse_log —
MagicMock style, no DB (mirrors test_agent_flow_node.py).
"""

from unittest.mock import MagicMock, patch

import pytest

from bisheng.workflow.callback.base_callback import BaseCallback
from bisheng.workflow.common.node import BaseNodeData, NodeGroupParams, NodeParams, NodeType
from bisheng.workflow.common.workflow import WorkflowStatus
from bisheng.workflow.graph.graph_state import GraphState
from bisheng.workflow.nodes.agent_flow.agent_flow import agent_flow_ancestors
from bisheng.workflow.nodes.loop.loop import LoopNode
from bisheng.workflow.nodes.node_manage import NodeFactory

INPUT = WorkflowStatus.INPUT.value
SUCCESS = WorkflowStatus.SUCCESS.value
FAILED = WorkflowStatus.FAILED.value

CONFIG = {
    "loop_var": "start_1.list",
    "item_var": "item",
    "flow_id": "callee_flow",
    "input_mapping": [{"field": "question", "mode": "var", "value": "loop_1.item"}],
    "outputs": [{"key": "echo", "label": "结果", "ref": "llm_1.output"}],
    "max_iterations": 100,
}


def _node_data(config: dict | None = None, **extra) -> BaseNodeData:
    config = config if config is not None else CONFIG
    return BaseNodeData(
        id="loop_1",
        type=NodeType.LOOP.value,
        name="循环",
        v=1,
        group_params=[
            NodeGroupParams(
                name="循环配置",
                params=[
                    NodeParams(
                        key="loop_config",
                        label="循环配置",
                        type="loop_config",
                        value=config,
                    )
                ],
            )
        ],
        **extra,
    )


def _make_node(config: dict | None = None, workflow_id: str = "parent_flow", user_id: int = 7) -> LoopNode:
    return NodeFactory.instance_node(
        node_type=NodeType.LOOP.value,
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
    flow.name = "循环体"
    flow.flow_type = 10  # WORKFLOW
    flow.status = 2  # ONLINE
    flow.data = {"nodes": [{"data": {"id": "llm_1", "type": "llm"}}], "edges": []}
    flow.tenant_id = 1
    flow.user_id = 2
    return flow


def _child_workflow(statuses: list, nodes_map: dict | None = None, pool: dict | None = None) -> MagicMock:
    child = MagicMock()
    child.run = MagicMock(side_effect=list(statuses))  # each item: (status, reason)
    child.graph_engine.nodes_map = nodes_map or {}
    child.graph_engine.graph_state.get_variable_by_str = MagicMock(side_effect=lambda ref: (pool or {}).get(ref))
    return child


def _patch_base(flow: MagicMock, children: list[MagicMock], permission: bool = True):
    from contextlib import ExitStack

    stack = ExitStack()
    stack.enter_context(patch("bisheng.workflow.nodes.loop.loop.FlowDao.get_flow_by_id", return_value=flow))
    stack.enter_context(
        patch(
            "bisheng.workflow.nodes.loop.loop.ApplicationPermissionService.has_any_permission_sync",
            return_value=permission,
        )
    )
    stack.enter_context(
        patch("bisheng.workflow.nodes.loop.loop.LoginUser.init_login_user_sync", return_value=MagicMock())
    )
    conf = MagicMock()
    conf.max_steps = 10
    conf.timeout = 30
    stack.enter_context(
        patch("bisheng.common.services.config_service.ConfigService.get_workflow_conf", return_value=conf)
    )
    stack.enter_context(patch("bisheng.workflow.graph.workflow.Workflow", side_effect=children))
    return stack


# --- happy path -----------------------------------------------------------------


def test_iterates_items_and_injects_each():
    """3 items → 3 fresh children; each run sees the current item in parent state."""
    flow = _callee_flow()
    seen_items: list = []
    children = []
    for _ in range(3):
        child = _child_workflow([(SUCCESS, "")])
        children.append(child)

    node = _make_node()
    node.graph_state.set_variable_by_str("start_1.list", ["a", "b", "c"])

    def _watch_run():
        seen_items.append(node.graph_state.get_variable_by_str("loop_1.item"))
        return (SUCCESS, "")

    for child in children:
        child.run = MagicMock(side_effect=_watch_run)

    with _patch_base(flow, children):
        result = node._run("exec_1")

    assert seen_items == ["a", "b", "c"]
    assert result == {"echo": [None, None, None]}


def test_outputs_collected_into_lists():
    """Two outputs → per-iteration values appended into unprefixed-key lists."""
    flow = _callee_flow()
    node = _make_node(
        {
            **CONFIG,
            "outputs": [
                {"key": "echo", "label": "结果", "ref": "llm_1.output"},
                {"key": "score", "label": "分数", "ref": "code_1.score"},
            ],
        }
    )
    node.graph_state.set_variable_by_str("start_1.list", [1, 2])
    children = []
    for i in range(2):
        children.append(
            _child_workflow(
                [(SUCCESS, "")],
                pool={"llm_1.output": "答案{}".format(i), "code_1.score": i},
            )
        )
    with _patch_base(flow, children):
        result = node._run("exec_1")

    assert result == {"echo": ["答案0", "答案1"], "score": [0, 1]}


def test_var_mode_mapping_resolves_item():
    """A var-mode mapping referencing loop_1.item resolves to the current item
    and is pre-fed into the child input node before the run."""
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
                name="表单",
                params=[
                    NodeParams(
                        key="form_input",
                        type="form",
                        value=[{"key": "question", "type": "text", "value": "", "label": "问题"}],
                    )
                ],
            )
        ],
    )
    child = _child_workflow([(SUCCESS, "")], nodes_map={"input_1": input_node})
    node = _make_node()
    node.graph_state.set_variable_by_str("start_1.list", ["机构A"])
    with _patch_base(flow, [child]):
        node._run("exec_1")

    assert child.run.call_count == 1
    input_node.handle_input.assert_called_once_with({"question": "机构A"})


def test_child_built_with_skip_input_interrupt():
    """The child Workflow is built with skip_input_interrupt=True (pre-fed inputs)."""
    flow = _callee_flow()
    child = _child_workflow([(SUCCESS, "")])
    node = _make_node()
    node.graph_state.set_variable_by_str("start_1.list", [1])
    kwargs_box: dict = {}

    def _factory(*args, **kwargs):
        kwargs_box.update(kwargs)
        return child

    with _patch_base(flow, [child]):
        with patch("bisheng.workflow.graph.workflow.Workflow", side_effect=_factory):
            node._run("exec_1")

    assert kwargs_box["skip_input_interrupt"] is True


def test_child_constructed_with_callee_tenant_and_creator():
    flow = _callee_flow()
    child = _child_workflow([(SUCCESS, "")])
    node = _make_node()
    node.graph_state.set_variable_by_str("start_1.list", [1])
    kwargs_box: dict = {}

    def _factory(*args, **kwargs):
        kwargs_box.update(kwargs)
        return child

    with _patch_base(flow, [child]):
        with patch("bisheng.workflow.graph.workflow.Workflow", side_effect=_factory):
            node._run("exec_1")

    assert kwargs_box["tenant_id"] == 1
    assert kwargs_box["flow_user_id"] == 2


def test_empty_list_returns_empty_lists_without_child():
    """Empty list → no child constructed, configured keys return empty lists."""
    flow = _callee_flow()
    node = _make_node()
    node.graph_state.set_variable_by_str("start_1.list", [])
    child = _child_workflow([(SUCCESS, "")])
    with _patch_base(flow, [child]):
        result = node._run("exec_1")

    assert result == {"echo": []}
    assert child.run.call_count == 0


# --- config & list errors ------------------------------------------------------


def test_non_list_loop_var_rejected():
    flow = _callee_flow()
    node = _make_node()
    node.graph_state.set_variable_by_str("start_1.list", "not-a-list")
    with _patch_base(flow, []):
        with pytest.raises(Exception, match="不是列表"):
            node._run("exec_1")


def test_missing_loop_var_rejected():
    node = _make_node({**CONFIG, "loop_var": ""})
    with pytest.raises(Exception, match="循环变量"):
        node._run("exec_1")


def test_missing_flow_id_rejected():
    node = _make_node({**CONFIG, "flow_id": ""})
    with pytest.raises(Exception, match="子流程"):
        node._run("exec_1")


def test_item_var_with_dot_rejected():
    node = _make_node({**CONFIG, "item_var": "a.b"})
    with pytest.raises(Exception, match="不能包含"):
        node._run("exec_1")


def test_max_iterations_truncates():
    flow = _callee_flow()
    node = _make_node({**CONFIG, "max_iterations": 2})
    node.graph_state.set_variable_by_str("start_1.list", [1, 2, 3, 4, 5])
    children = [_child_workflow([(SUCCESS, "")]) for _ in range(2)]
    with _patch_base(flow, children):
        result = node._run("exec_1")

    assert len(result["echo"]) == 2


# --- cycle guard ---------------------------------------------------------------


def test_self_call_rejected():
    node = _make_node({**CONFIG, "flow_id": "parent_flow"})
    with pytest.raises(Exception, match="循环"):
        node._run("exec_1")


def test_ancestor_chain_rejected_and_reset():
    flow = _callee_flow()
    child = _child_workflow([(SUCCESS, "")])
    node = _make_node({**CONFIG, "flow_id": "ancestor_flow"})
    node.graph_state.set_variable_by_str("start_1.list", [1])
    token = agent_flow_ancestors.set(("ancestor_flow", "root_flow"))
    try:
        with _patch_base(flow, [child]):
            with pytest.raises(Exception, match="循环"):
                node._run("exec_1")
    finally:
        agent_flow_ancestors.reset(token)
    assert agent_flow_ancestors.get() == ()


def test_chain_adds_own_workflow_id_during_child_run():
    flow = _callee_flow()
    child = _child_workflow([(SUCCESS, "")])
    node = _make_node()
    node.graph_state.set_variable_by_str("start_1.list", [1])
    seen: list = []

    def _watch_run(*a, **kw):
        seen.append(agent_flow_ancestors.get())
        return (SUCCESS, "")

    child.run = MagicMock(side_effect=_watch_run)
    with _patch_base(flow, [child]):
        node._run("exec_1")
    assert ("parent_flow",) in seen
    assert agent_flow_ancestors.get() == ()


# --- callee validations --------------------------------------------------------


def test_callee_not_found():
    node = _make_node()
    with patch("bisheng.workflow.nodes.loop.loop.FlowDao.get_flow_by_id", return_value=None):
        with pytest.raises(Exception, match="不存在"):
            node._run("exec_1")


def test_callee_wrong_type_rejected():
    flow = _callee_flow()
    flow.flow_type = 5  # ASSISTANT
    node = _make_node()
    with _patch_base(flow, []):
        with pytest.raises(Exception, match="工作流类型"):
            node._run("exec_1")


def test_callee_not_published_rejected():
    flow = _callee_flow()
    flow.status = 1  # OFFLINE
    node = _make_node()
    with _patch_base(flow, []):
        with pytest.raises(Exception, match="未发布"):
            node._run("exec_1")


def test_callee_empty_graph_rejected():
    flow = _callee_flow()
    flow.data = None
    node = _make_node()
    with _patch_base(flow, []):
        with pytest.raises(Exception, match="为空"):
            node._run("exec_1")


def test_permission_denied_rejected():
    flow = _callee_flow()
    node = _make_node()
    with _patch_base(flow, [], permission=False):
        with pytest.raises(Exception, match="无权"):
            node._run("exec_1")


# --- static non-interactivity checks -------------------------------------------


def _callee_with_input(tab_value: str | None, form_fields: list[dict] | None) -> MagicMock:
    flow = _callee_flow()
    node_data = {"id": "input_1", "type": "input", "name": "输入"}
    if tab_value is not None:
        node_data["tab"] = {"value": tab_value}
    if form_fields is not None:
        node_data["group_params"] = [
            {
                "name": "表单",
                "params": [{"key": "form_input", "type": "form", "value": form_fields}],
            }
        ]
    flow.data = {"nodes": [{"data": node_data}], "edges": []}
    return flow


def test_dialog_input_callee_rejected():
    flow = _callee_with_input("dialog_input", None)
    node = _make_node()
    with _patch_base(flow, []):
        with pytest.raises(Exception, match="对话输入"):
            node._run("exec_1")


def test_unmapped_form_field_rejected():
    flow = _callee_with_input("form_input", [{"key": "question", "type": "text"},
                                             {"key": "extra", "type": "text"}])
    node = _make_node()
    with _patch_base(flow, []):
        with pytest.raises(Exception, match="未映射"):
            node._run("exec_1")


def test_fully_mapped_form_callee_allowed():
    flow = _callee_with_input("form_input", [{"key": "question", "type": "text"}])
    child = _child_workflow([(SUCCESS, "")])
    node = _make_node()
    node.graph_state.set_variable_by_str("start_1.list", [1])
    with _patch_base(flow, [child]):
        result = node._run("exec_1")
    assert result == {"echo": [None]}


def test_file_form_fields_exempt_from_coverage():
    flow = _callee_with_input(
        "form_input",
        [{"key": "question", "type": "text"}, {"key": "attach", "type": "file"}],
    )
    child = _child_workflow([(SUCCESS, "")])
    node = _make_node()
    node.graph_state.set_variable_by_str("start_1.list", [1])
    with _patch_base(flow, [child]):
        node._run("exec_1")  # file field ignored → no error


# --- child run failures --------------------------------------------------------


def test_child_still_requires_input_rejected():
    """The child returns INPUT despite pre-fed inputs (e.g. interactive output) → rejected."""
    flow = _callee_with_input("form_input", [{"key": "question", "type": "text"}])
    input_node = MagicMock()
    input_node.id = "input_1"
    input_node.type = NodeType.INPUT.value
    input_node.node_data = BaseNodeData(id="input_1", type=NodeType.INPUT.value)
    child = _child_workflow([(INPUT, "")], nodes_map={"input_1": input_node})
    node = _make_node()
    node.graph_state.set_variable_by_str("start_1.list", [1])
    with _patch_base(flow, [child]):
        with pytest.raises(Exception, match="交互式输入"):
            node._run("exec_1")


def test_child_failed_carries_reason():
    flow = _callee_flow()
    child = _child_workflow([(FAILED, "循环体炸了")])
    node = _make_node()
    node.graph_state.set_variable_by_str("start_1.list", [1])
    with _patch_base(flow, [child]):
        with pytest.raises(Exception, match="循环体炸了"):
            node._run("exec_1")


# --- parse_log -----------------------------------------------------------------


def test_parse_log_shape():
    node = _make_node()
    log = node.parse_log("exec_1", {"echo": ["a", "b"]})
    assert log == [[{"key": "echo", "value": ["a", "b"], "type": "variable"}]]
