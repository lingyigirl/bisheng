from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from pydantic import ValidationError

from bisheng.common.constants.enums.telemetry import ApplicationTypeEnum
from bisheng.tool.domain.services.executor import ToolExecutor
from bisheng_langchain.gpts.tools.code_interpreter.tool import CodeInterpreterTool


def make_tool():
    return CodeInterpreterTool(executor=SimpleNamespace(run=Mock(return_value="391")))


def test_alias_executes_actual_code_without_modifying_caller_input():
    tool = make_tool()
    args = {"code": "print(17 * 23)"}
    assert tool.invoke(args) == "391"
    tool.executor.run.assert_called_once_with("print(17 * 23)")
    assert args == {"code": "print(17 * 23)"}


def test_telemetry_wrapper_preserves_alias_normalization():
    tool = make_tool()
    wrapper = ToolExecutor(
        tool_instance=tool,
        name=tool.name,
        description="code",
        args_schema=tool.args_schema,
        app_id="test",
        app_name="test",
        app_type=ApplicationTypeEnum.LINSIGHT,
        user_id=1,
        tool_id=6,
        tool_is_preset=1,
        tool_name="code",
    )
    assert wrapper._parse_input({"code": "print(1)"}, None) == {"python_code": "print(1)"}


@pytest.mark.parametrize("args", [{"code": "\n"}, {"python_code": "  "}, {}])
def test_missing_or_blank_code_never_reaches_executor(args):
    tool = make_tool()
    with pytest.raises(ValidationError):
        tool.invoke(args)
    tool.executor.run.assert_not_called()


def test_canonical_parameter_takes_precedence():
    tool = make_tool()
    tool.invoke({"python_code": "print(2)", "code": "print(1)"})
    tool.executor.run.assert_called_once_with("print(2)")


def test_wrapper_close_releases_sandbox_resources():
    tool = CodeInterpreterTool(executor=SimpleNamespace(close=Mock()))
    wrapper = ToolExecutor(
        tool_instance=tool,
        name=tool.name,
        description="code",
        args_schema=tool.args_schema,
        app_id="test",
        app_name="test",
        app_type=ApplicationTypeEnum.LINSIGHT,
        user_id=1,
        tool_id=6,
        tool_is_preset=1,
        tool_name="code",
    )
    wrapper.close()
    tool.executor.close.assert_called_once_with()
