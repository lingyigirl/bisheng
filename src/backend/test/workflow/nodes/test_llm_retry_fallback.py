"""F056-adjacent — workflow llm node _run_once retry/fallback policy.

Covers: empty-content retry cadence, the final-attempt behavior when the model
returns reasoning only (must RAISE, never substitute CoT as the node output —
it poisons strict-JSON extraction downstream), and APITimeoutError retries.
MagicMock style, no DB (mirrors test_agent_flow_node.py).
"""

from unittest.mock import MagicMock, patch

import pytest
from langchain_core.messages import AIMessage

from openai import APITimeoutError

from bisheng.workflow.nodes.llm.llm import LLMNode


def _make_node() -> LLMNode:
    node = LLMNode.__new__(LLMNode)
    node.id = "llm_1"
    node.name = "提取"
    node.callback_manager = MagicMock()
    node.user_id = 1
    node.workflow_id = "flow_x"
    node.node_params = {"model_id": 17}
    node._output_user = False
    node._image_prompt = []
    node._system_variables = []
    node._user_variables = []
    node._system_prompt = MagicMock()
    node._system_prompt.format.return_value = "sys"
    node._user_prompt = MagicMock()
    node._user_prompt.format.return_value = "user"
    node._system_prompt_list = []
    node._user_prompt_list = []
    node._log_reasoning_content = []
    node._llm = MagicMock()
    return node


def _patch_callback_with_reasoning(reasoning: str):
    """Patch LLMNodeCallbackHandler so `reasoning_content` carries CoT text."""
    def _fake_cls(*args, **kwargs):
        cb = MagicMock()
        cb.reasoning_content = reasoning
        cb.allow_empty_fallback = False
        return cb

    return patch("bisheng.workflow.nodes.llm.llm.LLMNodeCallbackHandler", side_effect=_fake_cls)


def test_retries_then_succeeds_with_content():
    node = _make_node()
    node._llm.invoke.side_effect = [
        AIMessage(content=""),
        AIMessage(content=""),
        AIMessage(content="合法 JSON"),
    ]
    with _patch_callback_with_reasoning("thinking..."):
        with patch("bisheng.workflow.nodes.llm.llm.time.sleep", return_value=None):
            content, reasoning = node._run_once(None, "exec_1", "output")

    assert content == "合法 JSON"
    assert node._llm.invoke.call_count == 3


def test_reasoning_only_final_attempt_raises_instead_of_substituting_cot():
    """Empty content × 3 with reasoning present → raise, never feed CoT as output."""
    node = _make_node()
    node._llm.invoke.side_effect = [AIMessage(content="")] * 3
    with _patch_callback_with_reasoning("我们只需要按要求提取数据……"):
        with patch("bisheng.workflow.nodes.llm.llm.time.sleep", return_value=None):
            with pytest.raises(Exception, match="仅返回思考过程"):
                node._run_once(None, "exec_1", "output")

    assert node._llm.invoke.call_count == 3


def test_reasoning_only_single_attempt_still_raises_on_third():
    """Even one empty attempt looped 3× raises (no silent empty output)."""
    node = _make_node()
    node._llm.invoke.return_value = AIMessage(content="")
    with _patch_callback_with_reasoning("covert reasoning"):
        with patch("bisheng.workflow.nodes.llm.llm.time.sleep", return_value=None):
            with pytest.raises(Exception, match="仅返回思考过程"):
                node._run_once(None, "exec_1", "output")


def test_timeout_retries_then_raises_on_final():
    node = _make_node()
    node._llm.invoke.side_effect = [APITimeoutError("408"), APITimeoutError("408"), APITimeoutError("408")]
    with patch("bisheng.workflow.nodes.llm.llm.time.sleep", return_value=None):
        with pytest.raises(APITimeoutError):
            node._run_once(None, "exec_1", "output")

    assert node._llm.invoke.call_count == 3


def test_timeout_then_success():
    node = _make_node()
    node._llm.invoke.side_effect = [APITimeoutError("408"), AIMessage(content="ok")]
    with patch("bisheng.workflow.nodes.llm.llm.time.sleep", return_value=None):
        content, _ = node._run_once(None, "exec_1", "output")

    assert content == "ok"
    assert node._llm.invoke.call_count == 2
