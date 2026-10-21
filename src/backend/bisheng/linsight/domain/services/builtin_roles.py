"""Built-in role presets for task-mode subagent delegation (2026-10-16).

dsh's answer to "zero-config specialised agents" is a shipped preset library
(``standard`` / ``code`` / ``cordis`` / ``minimal``) the model may pick from —
the model selects a composition, it never authors one. This module is the
Bisheng/Linsight equivalent: roles that are ALWAYS available, so a task with no
configured collaborators still gives the main agent a useful delegation menu
beyond the generic researcher.

Design decisions:

- Roles are CODE, not per-task data: no snapshot, no user config, no validation
  round-trip. Every run and every tenant sees the same definitions. This is the
  deliberate difference from ``configured_subagents`` (user-authored members,
  snapshot-validated, skill digests re-checked) — manual configuration stays the
  higher-trust path and is never shadowed: role names are fixed and cannot
  collide with ``general-purpose`` / ``member-<id>``.
- Tool policy reuses the caller's safe pool (``_subagent_tools``): a role can
  never see more than the main graph may use, and the HITL / write-side-effect
  blacklist applies unchanged. The caller passes the pool in so this module has
  no dependency on ``agent_factory`` (no import cycle).
- ``quality-reviewer`` is genuinely read-only: it gets NO extra tools, and the
  write tools deepagents injects via base middleware (``write_file`` /
  ``edit_file``) are denied in BOTH the model-visible tool list and at execution
  time — visibility alone is not enforcement (same principle as dsh's
  tool-filter note). read_file / ls / glob / grep / write_todos remain.
- Middleware comes from the SAME factory the configured collaborators use, so
  resilience / loop-breaker / binary guards / language tail cannot drift
  between the two subagent families.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from langchain.agents.middleware.types import AgentMiddleware
from langchain_core.messages import ToolMessage

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Sequence

    from langchain_core.tools import BaseTool

# Write-side fs tools injected by deepagents' FilesystemMiddleware. Denied for
# read-only roles in the model-visible list AND at execution time.
READ_ONLY_DENY = frozenset({"write_file", "edit_file"})


@dataclass(frozen=True)
class BuiltinRole:
    """One shipped role preset (deepagents ``SubAgent`` minus the plumbing)."""

    name: str
    description: str
    system_prompt: str
    read_only: bool = False


def _tool_name(tool: Any) -> str | None:
    """Tool name from a ``BaseTool`` or a dict tool spec (mirrors deepagents)."""
    if isinstance(tool, dict):
        name = tool.get("name")
        return name if isinstance(name, str) else None
    name = getattr(tool, "name", None)
    return name if isinstance(name, str) else None


class ReadOnlyGuardMiddleware(AgentMiddleware):
    """Deny fs write tools for a read-only role.

    ``awrap_model_call`` hides the schemas (the model never learns the call),
    ``awrap_tool_call`` rejects any invocation that still arrives (hallucinated,
    replayed, or injected). Placed OUTERMOST on the role's stack so the denial
    short-circuits before the rest of the middleware chain runs; the language
    tail stays innermost where its "absolute system-prompt tail" contract
    requires it.
    """

    async def awrap_model_call(self, request: Any, handler: Any) -> Any:
        filtered = [t for t in (request.tools or []) if _tool_name(t) not in READ_ONLY_DENY]
        if len(filtered) != len(request.tools or []):
            request = request.override(tools=filtered)
        return await handler(request)

    async def awrap_tool_call(self, request: Any, handler: Any) -> Any:
        tool_call = request.tool_call or {}
        name = tool_call.get("name")
        if name in READ_ONLY_DENY:
            return ToolMessage(
                content=(f"本角色为只读核查角色，禁止调用 {name}；请把需要修改的内容写成问题条目返回给主智能体。"),
                name=name,
                tool_call_id=tool_call.get("id"),
                status="error",
            )
        return await handler(request)


_DATA_ANALYST_PROMPT = """你是数据分析子代理（data-analyst）。你的职责是对工作区中的数据文件做定量分析，返回可复核的结论。

工作约定：
- 一律用代码解释器（bisheng_code_interpreter）做计算：读文件、清洗、统计，不靠心算或目测；关键数字要给出计算口径。
- 本次没有代码解释器工具时：用 read_file 读取后直接计算，并在结论里说明"未使用代码工具、按读取内容手工核算"。
- 动手分析前先 ls / read_file 确认真实存在的文件与字段，不要假设列名或数据范围。
- 中间产物（清洗后的数据、汇总表）写入任务描述指定的路径；描述未指定时写到 scratch/ 下，文件名保持清晰可辨。
- 你只对派发给你的单一子任务负责；不要撰写最终交付物正文（由主智能体完成）。
- 数据缺失或口径不明时，基于已有数据给出最佳结论并显式说明假设与不确定性，不要停下来等待澄清（你没有提问工具）。
- 调用方（主智能体）只能看到你的最后一条消息：把关键结论、计算口径与异常点完整写在最后一条消息里。"""

_CHART_DESIGNER_PROMPT = """你是图表可视化子代理（chart-designer）。你的职责是把工作区中的真实数据画成清晰、可用的图表文件。

工作约定：
- 用代码解释器（matplotlib）生成图表；中文字体已配置，注意负号、千分位与中文标签正常渲染。
- 直接复用环境中已注册的 CJK 字体族（如 Noto Sans CJK JP）；不要从 TTC/OTC 集合提取字体，也不要复制或保存字体文件。字体文件会触发大文件回收，可能把一个几秒的图表任务拖慢数分钟。
- 本次没有代码解释器工具时：不要假装出图——改为返回图表规格建议（用哪列数据、画什么图型、轴与标签怎么标），明确说明"无代码工具，未产出图片文件"。
- 图表必须来自真实数据：先 ls / read_file 找到数据文件并读取，绝不编造数据点。
- 输出 PNG 写到任务描述指定的路径；描述未指定时写到 scratch/charts/ 下，文件名用英文小写下划线。
- 每张图在汇报里给一句说明（图表类型、编码了什么数据、揭示的关键模式）；不要替主智能体撰写分析报告。
- 调用方只能看到你的最后一条消息：把图表文件清单与要点完整写在最后一条消息里。"""

_QUALITY_REVIEWER_PROMPT = """你是质检核查子代理（quality-reviewer）。你的职责是只读核查，找出问题，不改动任何文件。

工作约定：
- 你只有只读工具（ls / read_file / glob / grep）；没有写文件、编辑与代码解释器权限——想改什么，就把它写成问题条目交给主智能体。
- 核查对象由任务描述指定：通常是交付物（如 output/*.md）及其引用的源数据与中间产物。
- 重点核查：数字与源数据是否一致（可抽样）、单位与口径是否混淆、前后是否矛盾、图表与正文是否对应、结构完整性与明显事实错误、正文引用的产物路径是否真实存在。
- 输出一份问题清单：每条给出「位置 + 问题 + 依据 + 建议修订」，按严重度排序；没有发现问题也要明确写「未发现问题（核查范围：…）」。
- 调用方只能看到你的最后一条消息：把问题清单完整写在最后一条消息里。"""


BUILTIN_ROLES: tuple[BuiltinRole, ...] = (
    BuiltinRole(
        name="data-analyst",
        description=(
            "用于隔离的数据分析子任务：对工作区数据做清洗/统计/交叉核对，"
            "产出可复核的数据结论与中间数据文件。不能提问，不负责最终交付物撰写。"
        ),
        system_prompt=_DATA_ANALYST_PROMPT,
    ),
    BuiltinRole(
        name="chart-designer",
        description=(
            "用于隔离的图表制作子任务：按真实数据用代码解释器生成 PNG 图表（中文正常渲染）。"
            "不能提问，不负责最终交付物撰写。"
        ),
        system_prompt=_CHART_DESIGNER_PROMPT,
    ),
    BuiltinRole(
        name="quality-reviewer",
        description=(
            "只读质检子任务：对照源数据/其它产物核查交付物的一致性、数字口径与结构完整性，"
            "返回问题清单。不写任何文件、不能提问。"
        ),
        system_prompt=_QUALITY_REVIEWER_PROMPT,
        read_only=True,
    ),
)


def build_builtin_role_specs(
    safe_tools: Sequence[BaseTool],
    middleware_factory: Callable[[list[BaseTool]], list[AgentMiddleware]],
    *,
    artifact_contract: str,
    existing_names: Iterable[str] = (),
) -> list[dict]:
    """Build deepagents ``SubAgent`` specs for every shipped role.

    Args:
        safe_tools: the blacklist-filtered tool pool (``_subagent_tools``),
            computed by the caller so this module stays free of agent_factory
            imports (no cycle).
        middleware_factory: ``(child_tools) -> list[AgentMiddleware]`` — the
            SAME factory configured collaborators use, so guard rails match.
        artifact_contract: suffix that makes the child close its final message
            with the ``产物:`` line (T1 single artifact channel).
        existing_names: subagent names already on the graph; a role whose name
            is taken is skipped (defensive — fixed names cannot currently
            collide with ``general-purpose`` / ``member-<id>``).
    """
    taken = set(existing_names)
    specs: list[dict] = []
    for role in BUILTIN_ROLES:
        if role.name in taken:
            continue
        taken.add(role.name)
        role_tools = [] if role.read_only else list(safe_tools)
        middleware = list(middleware_factory(role_tools))
        if role.read_only:
            middleware = [ReadOnlyGuardMiddleware(), *middleware]
        specs.append(
            {
                "name": role.name,
                "description": role.description,
                "system_prompt": role.system_prompt + artifact_contract,
                "tools": role_tools,
                "middleware": middleware,
            }
        )
    return specs
