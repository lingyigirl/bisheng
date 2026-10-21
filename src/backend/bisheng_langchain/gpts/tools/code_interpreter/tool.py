import itertools
import os
import sys
from typing import Any

from langchain_core.tools import BaseTool
from pydantic import BaseModel, Field, field_validator

CODE_BLOCK_PATTERN = r"```(\w*)\n(.*?)\n```"
DEFAULT_TIMEOUT = 600
WIN32 = sys.platform == "win32"
PATH_SEPARATOR = (WIN32 and "\\") or "/"
WORKING_DIR = os.path.join(os.path.dirname(os.path.realpath(__file__)), "extensions")
TIMEOUT_MSG = "Timeout"
UNKNOWN = "unknown"


def infer_lang(code):
    """infer the language for the code.
    TODO: make it robust.
    """
    if code.startswith("python ") or code.startswith("pip") or code.startswith("python3 "):
        return "sh"

    # check if code is a valid python code
    try:
        compile(code, "test", "exec")
        return "python"
    except SyntaxError:
        # not a valid python code
        return UNKNOWN


def head_file(path: str, n: int) -> list[str]:
    """Get the first n lines of a file."""
    try:
        with open(path) as f:
            return [str(line) for line in itertools.islice(f, n)]
    except Exception:
        return []


class CodeInterpreterToolArguments(BaseModel):
    """Arguments for the BearlyInterpreterTool."""

    python_code: str = Field(
        ...,
        examples=["print('Hello World')"],
        description=(
            "The pure python script to be evaluated. "
            "The contents will be in main.py. "
            "It should not be in markdown format."
        ),
    )
    required_files: list[str] = Field(
        default_factory=list,
        description=(
            "Optional workspace-relative durable files required by this run. "
            "Use this for files above 5 MiB that must be restored after an E2B "
            "sandbox was recreated."
        ),
    )

    @field_validator("python_code")
    @classmethod
    def require_code(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("python_code must contain executable Python, not whitespace")
        return value


class CodeInterpreterTool(BaseTool):
    """Tool for evaluating python code in native environment."""

    name: str = "bisheng_code_interpreter"
    description: str = "code interpreter"
    args_schema: type[BaseModel] = CodeInterpreterToolArguments
    executor: Any = None

    def _parse_input(self, tool_input: str | dict, tool_call_id: str | None):
        # Normalize a common model alias before LangChain filters validated fields.
        if isinstance(tool_input, dict) and "code" in tool_input and "python_code" not in tool_input:
            tool_input = {**tool_input, "python_code": tool_input["code"]}
            tool_input.pop("code")
        return super()._parse_input(tool_input, tool_call_id)

    def _run(self, python_code: str, required_files: list[str] | None = None) -> dict:
        return self.executor.run(python_code, required_files=required_files or [])

    def close(self) -> None:
        self.executor.close()
