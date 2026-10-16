"""F076 T-18/T-19:`tools[]` 白名单语义(spec AC-29/30/31,INV-24)。

两条防线:
1. 服务层:解析/校验/清单/override 编译 —— 每条 allow 都配一个 deny(11815);
2. 接线(静态):端点必须真的把白名单交给执行链、必须在无法生效的形态下显式拒绝
   —— 这两处是薄胶水,行为难以用单测驱动(需要整栈夹具),用源码断言锁住
   (范式照 test/f040/test_static_scan.py),避免"服务写好了但没人调"。
"""

import pathlib

import pytest

from bisheng.common.errcode.open_api import OpenApiToolNotAllowedError
from bisheng.open_api.domain.schemas.tools import OpenApiToolPayload, parse_tool_selection
from bisheng.open_api.domain.services import tool_scope_service

BACKEND = pathlib.Path(__file__).resolve().parents[2]


class _StubTool:
    def __init__(self, tool_id: int, tool_key: str, name: str = "tool", desc: str | None = None):
        self.id = tool_id
        self.tool_key = tool_key
        self.name = name
        self.desc = desc


class _StubAssistant:
    def __init__(self, tools):
        self.tool_list = tools


def _workflow_data(*nodes):
    return {"nodes": list(nodes)}


def _agent_node(node_id: str, entries):
    return {
        "id": node_id,
        "data": {
            "type": "agent",
            "group_params": [{"params": [{"key": "tool_list", "value": entries}]}],
        },
    }


# ---------------------------------------------------------------------------
# 解析(形状)
# ---------------------------------------------------------------------------


def test_parse_absent_or_empty_means_unchanged():
    assert parse_tool_selection(None) is None
    assert parse_tool_selection([]) is None


def test_parse_rejects_non_list_and_bad_shape():
    with pytest.raises(OpenApiToolNotAllowedError):
        parse_tool_selection({"id": 1})
    with pytest.raises(OpenApiToolNotAllowedError):
        parse_tool_selection(["not-an-object"])


def test_parse_accepts_daily_chat_shape():
    parsed = parse_tool_selection([{"id": 3, "tool_key": "web_search", "type": "tool"}])
    assert parsed == [OpenApiToolPayload(id=3, tool_key="web_search", type="tool")]


# ---------------------------------------------------------------------------
# 助手侧:allow / deny 配对
# ---------------------------------------------------------------------------


def test_assistant_unset_keeps_default():
    info = _StubAssistant([_StubTool(1, "a")])
    assert tool_scope_service.resolve_assistant_tool_ids(info, None) is None


def test_assistant_accepts_bound_tools_by_key_and_id():
    info = _StubAssistant([_StubTool(1, "a"), _StubTool(2, "b")])
    assert tool_scope_service.resolve_assistant_tool_ids(info, [OpenApiToolPayload(tool_key="b")]) == [2]
    assert tool_scope_service.resolve_assistant_tool_ids(info, [OpenApiToolPayload(id=1)]) == [1]
    # 去重:同一条给两次
    assert tool_scope_service.resolve_assistant_tool_ids(
        info, [OpenApiToolPayload(tool_key="a"), OpenApiToolPayload(id=1)]
    ) == [1]


def test_assistant_rejects_unbound_or_unknown_tool():
    info = _StubAssistant([_StubTool(1, "a")])
    with pytest.raises(OpenApiToolNotAllowedError) as exc:
        tool_scope_service.resolve_assistant_tool_ids(info, [OpenApiToolPayload(tool_key="nope")])
    assert exc.value.kwargs.get("tool_key") == "nope"


def test_assistant_rejects_when_no_tool_bound():
    info = _StubAssistant([])
    with pytest.raises(OpenApiToolNotAllowedError):
        tool_scope_service.resolve_assistant_tool_ids(info, [OpenApiToolPayload(id=9)])


# ---------------------------------------------------------------------------
# 工作流侧:override 编译 / allow / deny 配对
# ---------------------------------------------------------------------------


def test_workflow_unset_keeps_default():
    data = _workflow_data(_agent_node("n1", [{"key": 1, "label": "A", "tool_key": "a"}]))
    assert tool_scope_service.resolve_workflow_tool_override(data, None) is None


def test_workflow_builds_whitelist_override_per_node():
    data = _workflow_data(
        _agent_node("n1", [{"key": 1, "label": "A", "tool_key": "a"}, {"key": 2, "label": "B", "tool_key": "b"}]),
        _agent_node("n2", [{"key": 2, "label": "B", "tool_key": "b"}]),
    )
    override = tool_scope_service.resolve_workflow_tool_override(data, [OpenApiToolPayload(tool_key="b")])

    # 白名单语义:n1 只留 B,没被选中的 n2 也要被显式清空(而不是照跑默认工具)
    assert override == {
        "n1": {"tool_list": [{"key": 2, "label": "B", "tool_key": "b"}]},
        "n2": {"tool_list": [{"key": 2, "label": "B", "tool_key": "b"}]},
    }

    override = tool_scope_service.resolve_workflow_tool_override(data, [OpenApiToolPayload(id=1)])
    assert override == {"n1": {"tool_list": [{"key": 1, "label": "A", "tool_key": "a"}]}, "n2": {"tool_list": []}}


def test_workflow_rejects_tool_bound_to_no_agent_node():
    data = _workflow_data(_agent_node("n1", [{"key": 1, "label": "A", "tool_key": "a"}]))
    with pytest.raises(OpenApiToolNotAllowedError) as exc:
        tool_scope_service.resolve_workflow_tool_override(data, [OpenApiToolPayload(tool_key="ghost")])
    assert exc.value.kwargs.get("tool_key") == "ghost"


def test_workflow_without_agent_nodes_rejects_tools():
    data = _workflow_data({"id": "n1", "data": {"type": "llm", "group_params": []}})
    with pytest.raises(OpenApiToolNotAllowedError):
        tool_scope_service.resolve_workflow_tool_override(data, [OpenApiToolPayload(tool_key="a")])


# ---------------------------------------------------------------------------
# T-19 清单形状
# ---------------------------------------------------------------------------


def test_assistant_manifest_shape():
    info = _StubAssistant([_StubTool(1, "a", name="联网搜索", desc="desc")])
    assert tool_scope_service.assistant_tool_manifest(info) == [
        {"id": 1, "tool_key": "a", "name": "联网搜索", "desc": "desc"}
    ]


def test_workflow_manifest_is_union_without_duplicates():
    data = _workflow_data(
        _agent_node("n1", [{"key": 1, "label": "A", "tool_key": "a"}]),
        _agent_node("n2", [{"key": 1, "label": "A", "tool_key": "a"}, {"key": 2, "label": "B", "tool_key": "b"}]),
    )
    manifest = tool_scope_service.workflow_tool_manifest(data)
    assert [item["id"] for item in manifest] == [1, 2]


# ---------------------------------------------------------------------------
# 接线守卫(静态源码断言):服务写好了,端点必须真的用上
# ---------------------------------------------------------------------------


def _read(rel: str) -> str:
    return (BACKEND / rel).read_text(encoding="utf-8")


def test_assistant_endpoint_passes_whitelist_into_execution():
    source = _read("bisheng/open_endpoints/api/endpoints/assistant.py")
    assert "resolve_assistant_tool_ids(" in source, "助手端点必须调用白名单解析"
    assert "enabled_tool_ids=enabled_tool_ids" in source, "白名单必须传进 AssistantAgent"


def test_agent_filters_tool_ids_before_executor():
    source = _read("bisheng/api/services/assistant_agent.py")
    filter_at = source.index("if tool_ids and self.enabled_tool_ids is not None")
    executor_at = source.index("ToolExecutor.init_by_tool_ids(", filter_at)
    assert filter_at < executor_at, "白名单过滤必须在 ToolExecutor 之前(执行链会静默丢弃无权限工具)"


def test_workflow_endpoint_guards_non_new_sessions():
    source = _read("bisheng/open_endpoints/api/endpoints/workflow.py")
    assert "resolve_workflow_tool_override(" in source
    assert "raise OpenApiToolScopeNotApplicableError()" in source, "续跑/运行中传 tools[] 必须显式拒绝(INV-24)"
    assert "copy.deepcopy(workflow_info.data)" in source, "注入前必须深拷贝,避免写脏 ORM JSON"
