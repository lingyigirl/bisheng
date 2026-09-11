"""F056 — agent_flow_schema service: input fields + output enumeration of a
callee workflow, parsed from raw flow.data (no node instantiation).

Covers: form/dialog input fields (file fields mappable since v2), output
enumeration per SelectVar global kinds, ref dedupe, permission gate and
missing-flow error.
"""

from unittest.mock import MagicMock, patch

import pytest

from bisheng.api.services.workflow import WorkFlowService
from bisheng.common.errcode.http_error import NotFoundError, UnAuthorizedError

FLOW_DATA = {
    "nodes": [
        {
            "data": {
                "id": "input_1",
                "type": "input",
                "name": "输入",
                "tab": {"value": "form_input"},
                "group_params": [
                    {
                        "name": "输入",
                        "params": [
                            {
                                "key": "form_input",
                                "type": "form",
                                "value": [
                                    {"key": "question", "type": "text", "value": "", "label": "问题"},
                                    {"key": "attach", "type": "file", "value": "", "label": "附件"},
                                ],
                            }
                        ],
                    }
                ],
            }
        },
        {
            "data": {
                "id": "llm_1",
                "type": "llm",
                "name": "大模型",
                "group_params": [
                    {
                        "name": "输出",
                        "params": [
                            {
                                "key": "output",
                                "global": "code:value.map(el => ({ label: el.label, value: el.key }))",
                                "label": "true",
                                "value": [],
                            },
                            {
                                "key": "batch_variable",
                                "global": "self",
                                "label": "true",
                                "value": [],
                            }
                        ],
                    }
                ],
            }
        },
        {
            "data": {
                "id": "agent_1",
                "type": "agent",
                "name": "助手",
                "group_params": [
                    {
                        "name": "输出",
                        "params": [
                            {
                                "key": "output",
                                "global": "code:value.map(el => ({label: el.label, value: el.key}))",
                                "value": [
                                    {"key": "answer", "label": "回答"},
                                    {"key": "steps", "label": "步骤"},
                                ],
                            }
                        ],
                    }
                ],
            }
        },
        {
            "data": {
                "id": "input_2",
                "type": "input",
                "name": "对话输入",
                "tab": {"value": "dialog_input"},
                "group_params": [],
            }
        },
    ],
    "edges": [],
}


def _flow() -> MagicMock:
    flow = MagicMock()
    flow.name = "客服问答"
    flow.flow_type = 10  # WORKFLOW
    flow.data = FLOW_DATA
    return flow


def _login_user() -> MagicMock:
    return MagicMock()


def test_input_fields_and_file_supported_flag():
    flow = _flow()
    with (
        patch("bisheng.api.services.workflow.FlowDao.get_flow_by_id", return_value=flow),
        patch("bisheng.api.services.workflow.ApplicationPermissionService.has_any_permission_sync", return_value=True),
    ):
        schema = WorkFlowService.get_agent_flow_schema(_login_user(), "callee_flow")

    assert schema["flow_name"] == "客服问答"
    inputs = {i["node_id"]: i for i in schema["input"]}
    assert set(inputs) == {"input_1", "input_2"}
    form_fields = {f["key"]: f for f in inputs["input_1"]["fields"]}
    assert form_fields["question"]["supported"] is True
    assert form_fields["attach"]["supported"] is True  # v2: file fields are mappable
    dialog_fields = inputs["input_2"]["fields"]
    assert dialog_fields[0]["key"] == "user_input"


def test_output_enumeration_and_dedupe():
    flow = _flow()
    flow.data["nodes"].append(
        # duplicate ref of llm_1.output via a second node param → deduped
        {
            "data": {
                "id": "llm_1",
                "type": "llm",
                "group_params": [{"name": "x", "params": [{"key": "output", "global": "key", "label": "输出2"}]}],
            }
        }
    )
    with (
        patch("bisheng.api.services.workflow.FlowDao.get_flow_by_id", return_value=flow),
        patch("bisheng.api.services.workflow.ApplicationPermissionService.has_any_permission_sync", return_value=True),
    ):
        schema = WorkFlowService.get_agent_flow_schema(_login_user(), "callee_flow")

    refs = [o["ref"] for o in schema["outputs"]]
    assert "llm_1.output" in refs  # code: empty list → output* fallback
    assert "agent_1.answer" in refs
    assert "agent_1.steps" in refs
    assert "llm_1.batch_variable" not in refs  # self globals are node-private
    assert len(refs) == len(set(refs))  # dedupe by ref


def test_missing_flow_raises_not_found():
    with (
        patch("bisheng.api.services.workflow.FlowDao.get_flow_by_id", return_value=None),
    ):
        with pytest.raises(NotFoundError):
            WorkFlowService.get_agent_flow_schema(_login_user(), "nope")


def test_non_workflow_flow_raises_not_found():
    flow = _flow()
    flow.flow_type = 5  # ASSISTANT — not selectable as a callee
    with (
        patch("bisheng.api.services.workflow.FlowDao.get_flow_by_id", return_value=flow),
    ):
        with pytest.raises(NotFoundError):
            WorkFlowService.get_agent_flow_schema(_login_user(), "assistant_flow")


def test_permission_denied_raises():
    flow = _flow()
    with (
        patch("bisheng.api.services.workflow.FlowDao.get_flow_by_id", return_value=flow),
        patch("bisheng.api.services.workflow.ApplicationPermissionService.has_any_permission_sync", return_value=False),
    ):
        with pytest.raises(UnAuthorizedError):
            WorkFlowService.get_agent_flow_schema(_login_user(), "callee_flow")
