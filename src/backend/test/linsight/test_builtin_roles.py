"""Unit tests for the built-in role presets (builtin_roles.py, 2026-10-16).

The shipped roles are the dsh-style answer to "zero-config specialised agents":
always available, no snapshot, no user config. These tests lock the safety
invariants that make appending them unconditionally safe:

- specs carry the T1 artifact contract and the researcher's safety shape
  (no ``model`` / ``permissions`` / ``interrupt_on`` keys);
- ``data-analyst`` / ``chart-designer`` draw from the SAME blacklist-filtered
  pool as the researcher (never more than the main graph may use);
- ``quality-reviewer`` is genuinely read-only: NO extra tools, and the
  framework-injected write tools are denied in both the model-visible list and
  at execution time (visibility alone is not enforcement);
- roles never shadow an existing subagent name.
"""

from __future__ import annotations

from langchain_core.messages import ToolMessage

from bisheng.linsight.domain.services.agent_factory import (
    SUBAGENT_ARTIFACT_REPORT_CONTRACT,
    _build_linsight_system_prompt,
    _subagent_tools,
)
from bisheng.linsight.domain.services.builtin_roles import (
    BUILTIN_ROLES,
    READ_ONLY_DENY,
    ReadOnlyGuardMiddleware,
    build_builtin_role_specs,
)


class _FakeTool:
    """Minimal BaseTool-like stub: the helpers only read ``.name``."""

    def __init__(self, name: str):
        self.name = name


class _SentinelMiddleware:
    """Marker middleware so tests can assert stack assembly and ordering."""

    def __init__(self, tag: str):
        self.tag = tag


def _factory(tag: str = "stack"):
    return lambda _tools: [_SentinelMiddleware(f"{tag}-1"), _SentinelMiddleware(f"{tag}-2")]


def _pool() -> list:
    return [_FakeTool("my_mcp_tool"), _FakeTool("search_knowledge_base")]


def _build(existing=()):
    return build_builtin_role_specs(
        _pool(),
        _factory(),
        artifact_contract=SUBAGENT_ARTIFACT_REPORT_CONTRACT,
        existing_names=existing,
    )


def test_roles_are_unique_and_cover_the_shipped_menu():
    names = [role.name for role in BUILTIN_ROLES]
    assert len(names) == len(set(names))
    assert set(names) == {"data-analyst", "chart-designer", "quality-reviewer"}


def test_specs_carry_artifact_contract_and_researcher_safety_shape():
    specs = {spec["name"]: spec for spec in _build()}

    assert set(specs) == {"data-analyst", "chart-designer", "quality-reviewer"}
    for spec in specs.values():
        assert spec["description"]
        assert spec["system_prompt"].endswith(SUBAGENT_ARTIFACT_REPORT_CONTRACT)
        assert "model" not in spec, "omitting model => inherits parent tenant model"
        assert "permissions" not in spec, "permissions would derive fs interrupts"
        assert "interrupt_on" not in spec, "interrupt_on would add HITL middleware"


def test_full_pool_roles_share_the_safe_blacklist_filtered_pool():
    plain_tools = [
        _FakeTool("my_mcp_tool"),
        _FakeTool("search_knowledge_base"),
        _FakeTool("ask_user"),
        _FakeTool("add_text_to_file"),
        _FakeTool("export_docx"),
    ]
    expected = {t.name for t in _subagent_tools(plain_tools)}
    specs = {spec["name"]: spec for spec in build_builtin_role_specs(
        _subagent_tools(plain_tools),
        _factory(),
        artifact_contract=SUBAGENT_ARTIFACT_REPORT_CONTRACT,
    )}

    for role in ("data-analyst", "chart-designer"):
        names = {t.name for t in specs[role]["tools"]}
        assert names == expected
        assert names.isdisjoint({"ask_user", "add_text_to_file", "export_docx"})

    # Reviewer gets NO extra tools; its fs tools come from base middleware.
    assert specs["quality-reviewer"]["tools"] == []


def test_read_only_guard_is_outermost_and_stack_is_preserved():
    specs = {spec["name"]: spec for spec in _build()}
    reviewer = specs["quality-reviewer"]["middleware"]
    assert isinstance(reviewer[0], ReadOnlyGuardMiddleware)
    # Factory output (sentinel stack) survives, in order, behind the guard.
    assert [getattr(m, "tag", None) for m in reviewer[1:]] == ["stack-1", "stack-2"]

    analyst = specs["data-analyst"]["middleware"]
    assert not any(isinstance(m, ReadOnlyGuardMiddleware) for m in analyst)


def test_roles_skip_names_already_taken():
    specs = _build(existing=["data-analyst", "quality-reviewer"])
    assert [spec["name"] for spec in specs] == ["chart-designer"]
    assert _build(existing=[role.name for role in BUILTIN_ROLES]) == []


class _FakeRequest:
    """ToolCallRequest / ModelRequest stand-in with override()."""

    def __init__(self, tool_call=None, *, tools=None):
        self.tool_call = tool_call or {}
        self.tools = tools or []

    def override(self, **kwargs):
        clone = _FakeRequest(self.tool_call, tools=list(self.tools))
        for key, value in kwargs.items():
            setattr(clone, key, value)
        return clone


async def test_read_only_guard_hides_write_tools_from_the_model():
    guard = ReadOnlyGuardMiddleware()
    seen: dict = {}

    async def handler(request):
        seen["tools"] = [t.name for t in request.tools]
        return "model"

    request = _FakeRequest(tools=[_FakeTool("read_file"), _FakeTool("write_file"), _FakeTool("grep")])
    await guard.awrap_model_call(request, handler)
    assert seen["tools"] == ["read_file", "grep"]


async def test_read_only_guard_denies_write_calls_at_execution_time():
    guard = ReadOnlyGuardMiddleware()
    called: dict = {}

    async def handler(_request):
        called["hit"] = True
        return "ran"

    denied = await guard.awrap_tool_call(
        _FakeRequest({"name": "write_file", "args": {"file_path": "/x"}, "id": "tc1"}), handler
    )
    assert isinstance(denied, ToolMessage)
    assert denied.status == "error"
    assert "只读" in denied.content
    assert "hit" not in called, "the write tool must never execute"

    allowed = await guard.awrap_tool_call(_FakeRequest({"name": "read_file", "args": {}, "id": "tc2"}), handler)
    assert allowed == "ran"
    assert called["hit"] is True


def test_read_only_deny_covers_exactly_the_fs_write_tools():
    assert READ_ONLY_DENY == {"write_file", "edit_file"}


def test_main_prompt_advertises_the_builtin_roles():
    prompt = _build_linsight_system_prompt(has_knowledge_base=False)
    for role in ("general-purpose", "data-analyst", "chart-designer", "quality-reviewer"):
        assert role in prompt
