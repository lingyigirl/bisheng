"""Tool-call loop breaker middleware for Linsight task mode (Layer 3).

Design: 灵思任务模式 write_file 死循环根因分析与优化方案 (Layer 3 / Layer 4).

A weak model can get stuck calling the same tool with the same broken arguments
forever — most notably ``write_file`` whose large ``content`` argument gets
truncated (``finish_reason=length``) and dropped by ``parse_partial_json``, so
every retry re-raises ``content: Field required``. Nothing in the stack counts
repeated identical tool failures, so the only backstop is ``recursion_limit``
(200) — ~19 minutes of wasted spinning ending in a raw ``GraphRecursionError``.

The mirror-image loop is covered too: a model that keeps calling a tool
*successfully* while getting the IDENTICAL output back (2026-10-15 — 15+
``os.walk('/root/.cache')`` scans each printing ``0``, then existence checks
printing ``[] / False``, until the user terminated the task). Nothing "fails"
there, so no error-based counter ever moves; the output-identity streak is what
bounds it: soft nudge once the same successful result repeats ``soft_limit``
times, hard stop at ``hard_limit``.

Phase 2 (2026-10-16, live run b6014d82): consecutive-streak counting cannot see
an INTERLEAVED loop. A real run cycled ``[read 5 pages -> tweak the build
script]`` for ten minutes — every read broke the previous read-streak, and every
code call carried slightly different args, so neither counter ever advanced.
Two order-insensitive rules close that gap (both stateless, computed from the
message list like everything else here):

- **windowed identity repeats**: the same (args + result) identity appearing
  ``soft_limit`` / ``hard_limit`` times within the last ``window`` tool results,
  regardless of what sits between them;
- **tool-name cycle**: the trailing tool-name sequence being the SAME pattern
  repeated ``cycle_soft_rounds`` / ``cycle_hard_rounds`` times, gated by
  same-position identity repetition between the last two rounds (a legitimate
  batch over different items re-uses the pattern shape but never the identities).

This ``AgentMiddleware`` bounds that loop, mirroring the built-in
``ToolCallLimitMiddleware`` pattern (raise from ``after_model`` — the one hook
whose exception propagates cleanly out of ``astream``, unlike ``wrap_tool_call``
whose raises are swallowed by ``ToolNode``):

- ``awrap_tool_call`` — SOFT nudge: once the same tool has failed ``soft_limit``
  times in a row, append a stronger corrective hint to the error ``ToolMessage``
  so a recoverable model can fix itself (e.g. "split the doc / put the full text
  in content").
- ``aafter_model`` — HARD stop: once the same tool has failed ``hard_limit``
  times in a row AND the model is STILL trying to call it, salvage the run's
  intermediate analysis + retrieved knowledge from the message history and raise
  ``LinsightToolLoopError(partial_result=...)``. ``task_exec`` renders that as a
  normal (COMPLETED) result with an apology preamble instead of a raw recursion
  error — the user still gets meaningful output.

One instance per graph (main + researcher subagent), matching
``build_resilience_middleware`` — see ``build_tool_loop_breaker_middleware``.
"""

from __future__ import annotations

import json
from typing import Any

from langchain.agents.middleware.types import AgentMiddleware
from langchain_core.messages import ToolMessage
from loguru import logger

# The knowledge-search tool name (SearchKnowledgeBase). Kept as a local literal so
# this module does not import agent_factory (which imports this module).
_KB_TOOL_NAME = "search_knowledge_base"

# Salvage digest caps (D4-a: 精简结果 — analysis prioritized, retrieved chunks
# trimmed so we never dump dozens of pages of raw broker research verbatim).
_SALVAGE_ANALYSIS_CAP = 8000
_SALVAGE_KB_PER_CAP = 800
_SALVAGE_KB_TOTAL_CAP = 4000
_TRUNCATION_NOTE = "\n\n…（内容较长，已截断）"

# Phase-2 (cycle detection) constants.
_TOOL_CYCLE_MIN_PERIOD = 2
_TOOL_CYCLE_MAX_PERIOD = 6
# One shared walk cap for the order-insensitive rules: large enough for
# cycle_hard_rounds (5) full periods even at the max period (6 -> 30) with slack.
_TOOL_ENTRY_WALK_LIMIT = 48

# Retrieval-budget rule (2026-10-16 live run bdffc2b2): a main-graph run issued
# 40+ web_search calls whose results kept collapsing to the same handful of
# articles, until the user terminated it. Every query differed slightly, so the
# identity rules never matched and the cycle rule never fires on a single
# repeated tool name (needs >= 2 names). Only RETRIEVAL tools get this budget:
# they are semantically close to idempotent (a dozen queries cover most of what
# forty would), while read_file / code runs legitimately repeat with different
# arguments.
_SEARCH_TOOL_NAMES = frozenset({"web_search", _KB_TOOL_NAME})


class LinsightToolLoopError(Exception):
    """Raised when one tool loops ``hard_limit`` times in a row.

    Two shapes, both aborting with a salvaged partial result so ``task_exec``
    renders a meaningful outcome instead of a raw recursion error:

    - ``reason="failure"`` — the same tool failed ``hard_limit`` times;
    - ``reason="no_progress"`` — the same tool returned byte-identical output
      ``hard_limit`` times, i.e. the loop is made of "successes" (the failure
      counter never moves for those);
    - ``reason="repeat"`` — the same (args + result) identity appeared
      ``hard_limit`` times within the recent window, interleaving included;
    - ``reason="cycle"`` — the trailing tool-name sequence repeated the same
      ``period``-step pattern ``count`` times with recurring identities.
    - ``reason="search_budget"`` — a retrieval tool (web_search /
      search_knowledge_base) was called ``count`` times within the recent
      window with ever-changing arguments, i.e. a re-query loop no identity
      rule can see.
    """

    def __init__(
        self,
        *,
        tool_name: str | None,
        count: int,
        partial_result: str = "",
        reason: str = "failure",
        period: int | None = None,
    ) -> None:
        self.tool_name = tool_name
        self.count = count
        self.partial_result = partial_result or ""
        self.reason = reason
        self.period = period
        if reason == "no_progress":
            detail = f"returned identical output {count} times consecutively"
        elif reason == "repeat":
            detail = f"returned the same args+result {count} times within the recent window"
        elif reason == "cycle":
            detail = f"repeated the same {period}-step tool cycle {count} times"
        elif reason == "search_budget":
            detail = (
                f"kept calling {tool_name} despite {count} retrieval calls within the recent window"
            )
        else:
            detail = f"failed {count} times consecutively"
        super().__init__(f"Tool '{tool_name}' {detail}; aborting the task with a salvaged partial result.")


# ---------------------------------------------------------------------------
# Message-shape-tolerant helpers (dict OR message object; mirrors
# agent_factory._empty_retry_count's tolerance).
# ---------------------------------------------------------------------------


def _msg_fields(m: object) -> tuple[str | None, str | None, str | None, Any]:
    """Extract (role/type, name, status, content) from a message (dict or object)."""
    if isinstance(m, dict):
        return (m.get("type") or m.get("role"), m.get("name"), m.get("status"), m.get("content"))
    return (
        getattr(m, "type", None),
        getattr(m, "name", None),
        getattr(m, "status", None),
        getattr(m, "content", None),
    )


def _state_messages(state: object) -> list:
    if isinstance(state, dict):
        return state.get("messages") or []
    return getattr(state, "messages", None) or []


def _content_to_text(content: Any) -> str:
    """Flatten message content (str or multimodal list) to plain text."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict) and isinstance(block.get("text"), str):
                parts.append(block["text"])
        return "".join(parts)
    return "" if content is None else str(content)


def _cap(text: str, limit: int) -> str:
    text = text.strip()
    if len(text) <= limit:
        return text
    return text[:limit] + _TRUNCATION_NOTE


def _last_ai_message(messages: list) -> object | None:
    for m in reversed(messages):
        role, _, _, _ = _msg_fields(m)
        if role in ("ai", "assistant"):
            return m
    return None


def _tool_calls_of(message: object) -> list[dict]:
    if message is None:
        return []
    if isinstance(message, dict):
        calls = message.get("tool_calls")
    else:
        calls = getattr(message, "tool_calls", None)
    return calls or []


def _trailing_tool_failure_run(messages: list) -> tuple[str | None, int]:
    """(tool_name, count) of the trailing run of consecutive same-tool error
    ``ToolMessage``s.

    AIMessages (the tool-call producers, interleaved between each failure) are
    transparent; a non-error ToolMessage (a success), a different tool, or a
    human turn ends the run. Returns ``(None, 0)`` when the most recent tool
    result was not an error.
    """
    target: str | None = None
    count = 0
    for m in reversed(messages):
        role, name, status, _ = _msg_fields(m)
        if role in ("human", "user"):
            break
        if role in ("ai", "assistant"):
            continue  # tool-call producer — transparent to the run
        if role == "tool":
            if status != "error":
                break  # a success (or non-error) result ends the failure run
            if target is None:
                target, count = name, 1
            elif name == target:
                count += 1
            else:
                break  # a different tool's failure ends the same-tool run
            continue
        # Any other message kind (system, etc.): be conservative and stop.
        break
    return target, count


def _same_tool_streak(messages: list, tool_name: str | None) -> int:
    """Length of the trailing same-tool error run IF it matches ``tool_name``."""
    run_tool, run_count = _trailing_tool_failure_run(messages)
    return run_count if run_tool == tool_name else 0


# Marker prefixing the appended no-progress nudge. The streak walk strips it
# before comparing, so nudging a streak does not make the NEXT identical result
# look "different" (which would reset the counter and defeat the hard stop).
_NO_PROGRESS_MARKER = "\n\n[no-progress] "
# Marker for the phase-2 tool-name-cycle nudge. Also stripped by
# ``_strip_no_progress_hint`` so appending it never changes a result's identity
# (which would break the very counters that produced it).
_CYCLE_MARKER = "\n\n[cycle] "
# Marker for the retrieval-budget nudge (same search tool, any args).
_SEARCH_BUDGET_MARKER = "\n\n[search-budget] "


def _strip_no_progress_hint(text: str) -> str:
    """Content with any previously appended internal nudge removed.

    Handles all three markers: a result can carry any combination of the
    no-progress, cycle and search-budget markers — the earliest marker wins.
    """
    cut = len(text)
    for marker in (_NO_PROGRESS_MARKER, _CYCLE_MARKER, _SEARCH_BUDGET_MARKER):
        idx = text.find(marker)
        if idx != -1:
            cut = min(cut, idx)
    return text[:cut]


def _no_progress_hint(tool_name: str | None) -> str:
    target = f"调用 {tool_name} " if tool_name else "工具调用"
    return (
        f"⚠️ 你已连续多次{target}并拿到了完全相同的结果，没有任何新信息。"
        "这说明当前做法走不通：不要再用同样的方式重试——换一种方法（换路径 / 换工具 / 先核对前提条件），"
        "或者如果确实无法推进，就基于已有材料如实收尾。"
    )


def _repeat_hint(tool_name: str | None, count: int) -> str:
    """Nudge for the windowed same-identity rule (interleaving included)."""
    target = f"调用 {tool_name} " if tool_name else "工具调用"
    return (
        f"⚠️ 最近的连续操作中，你已经 {count} 次{target}并拿到完全相同的参数与结果"
        "（中间还夹着其它步骤）。重复同一件事不会带来新信息：换一种方法（换路径 / 换工具 / 先核对前提），"
        "或者如果确实无法推进，就基于已有材料如实收尾。"
    )


def _cycle_hint(period: int, rounds: int) -> str:
    """Nudge for the repeated-tool-name-pattern rule."""
    return (
        f"⚠️ 检测到你正在重复同一套 {period} 步操作节奏（已第 {rounds} 轮），每一轮的动作与上一轮重复。"
        "如果这是对多个不同对象的批量处理，请忽略本提示；如果是在反复打磨同一个产物/同一件事，"
        "请立刻收敛：基于当前结果收尾，不要再进入下一轮。"
    )


def _search_budget_hint(tool_name: str | None, count: int, *, is_subagent: bool) -> str:
    """Nudge for the retrieval-budget rule (same search tool, ANY args)."""
    target = tool_name or "检索工具"
    if is_subagent:
        return (
            f"⚠️ 你近期已调用 {target} {count} 次且仍在继续。检索工具的边际收益递减很快——"
            "如果结果已开始重复或不再有新增信息，请立即基于已收集的资料完成回传，不要继续换词重搜。"
        )
    return (
        f"⚠️ 本任务近期已调用 {target} {count} 次且仍在继续。参数不同但结果高度重复时，"
        "继续换关键词搜索不会带来新信息：请停止自行检索，改用 task 委派资料研究员（general-purpose）接管检索，"
        "或直接基于已收集的资料进入撰写与收尾。"
    )


def _windowed_tool_name_count(entries: list[tuple[str | None, str]], tool_name: str, window: int) -> int:
    """How many of the last ``window`` tool entries are calls of ``tool_name``
    (arguments deliberately ignored). Feeds the retrieval-budget rule."""
    return sum(1 for name, _ in entries[-window:] if name == tool_name)


def _content_identity(content: Any) -> str:
    """Comparable identity for one tool result.

    Plain strings compare as-is (a previously appended ``[no-progress]`` nudge
    is stripped). Structured content — multimodal blocks, e.g. ``read_file`` of
    a ``slide-XX.jpg`` returning an image — has no flattened text, and turning
    it into "" made every DIFFERENT image look identical: 2026-10-15 the ppt
    turn rendered slides for visual QA and the breaker aborted it after
    "8 identical outputs" that were actually eight distinct renders. Canonical
    JSON keeps the blocks (base64 included) comparable: different images
    differ, the SAME image read twice still matches.
    """
    if isinstance(content, str):
        return _strip_no_progress_hint(content)
    try:
        return json.dumps(content, sort_keys=True, default=str, ensure_ascii=False)
    except Exception:
        return _strip_no_progress_hint(_content_to_text(content))


def _args_digest(args: Any) -> str:
    """Canonical digest of one tool call's arguments (an identity component —
    see ``_no_progress_identity``)."""
    try:
        return json.dumps(args, sort_keys=True, default=str, ensure_ascii=False)
    except Exception:
        return str(args)


def _no_progress_identity(args_digest: str, content: Any) -> str:
    """Identity of one SUCCESSFUL tool call: its arguments + its result.

    2026-10-16 (b7e118a6): ``edit_file`` answers every successful edit with the
    SAME confirmation string ("Successfully replaced 1 instance(s) of the
    string in '...'"). Identity on the result ALONE made a report turn that
    edited one section at a time — adding figure refs, correcting counts —
    look like "8 identical outputs" and the breaker aborted two healthy tasks
    with a "model is looping" apology. Including the call's arguments keeps
    genuinely different work apart while a TRUE loop (same file, same edit,
    same confirmation) still counts.
    """
    return f"{args_digest}\x00{_content_identity(content)}"


def _tool_call_id(message: object) -> Any:
    if isinstance(message, dict):
        return message.get("tool_call_id")
    return getattr(message, "tool_call_id", None)


def _tool_args_by_id(messages: list) -> dict:
    """``tool_call_id -> args digest`` from every assistant tool_call present."""
    args_by_id: dict = {}
    for m in messages:
        role, _, _, _ = _msg_fields(m)
        if role in ("ai", "assistant"):
            for call in _tool_calls_of(m):
                if isinstance(call, dict) and call.get("id"):
                    args_by_id[str(call["id"])] = _args_digest(call.get("args"))
    return args_by_id


def _trailing_no_progress_run(messages: list) -> tuple[str | None, str | None, int]:
    """(tool_name, base_content, count) of the trailing run of successful
    ``ToolMessage``s from the same tool with identical ARGUMENTS + content.

    Identity via ``_no_progress_identity`` (arguments + ``_content_identity``:
    strings strip a previously appended nudge so a nudged streak keeps
    counting; structured content compares canonically). Arguments matter
    because several tools answer everything with a constant confirmation
    string — see ``_no_progress_identity`` for the 2026-10-16 edit_file
    false-positive this prevented. A tool message whose call id is unknown (old
    transcripts / fixtures without the assistant rows) falls back to an empty
    args digest, i.e. result-only identity. An error result ends the run (the
    failure streak owns those); AIMessages are transparent; a human turn or any
    other message kind stops the walk. Returns ``(None, None, 0)`` when the
    most recent tool result is an error.
    """
    args_by_id = _tool_args_by_id(messages)
    target: str | None = None
    base: str | None = None
    count = 0
    for m in reversed(messages):
        role, name, status, content = _msg_fields(m)
        if role in ("human", "user"):
            break
        if role in ("ai", "assistant"):
            continue  # tool-call producer — transparent to the run
        if role == "tool":
            if status == "error":
                break  # failure runs are counted by the error streak
            normalized = _no_progress_identity(
                args_by_id.get(str(_tool_call_id(m)), ""), content
            )
            if target is None:
                target, base, count = name, normalized, 1
            elif name == target and normalized == base:
                count += 1
            else:
                break
            continue
        # Any other message kind (system, etc.): be conservative and stop.
        break
    return target, base, count


def _trailing_tool_entries(messages: list, limit: int = _TOOL_ENTRY_WALK_LIMIT) -> list[tuple[str | None, str]]:
    """``(tool_name, identity)`` of the trailing tool results, oldest -> newest.

    Unlike ``_trailing_no_progress_run`` this walk does NOT stop at interleaving:
    every tool result (success or error) in the trailing stretch is included, so
    the order-insensitive phase-2 rules can count repeats regardless of what sits
    between them. A human turn or any non-tool/AI message still ends the walk
    (never count across a user turn); the walk is capped at ``limit`` entries.
    """
    args_by_id = _tool_args_by_id(messages)
    entries: list[tuple[str | None, str]] = []
    for m in reversed(messages):
        role, name, _status, content = _msg_fields(m)
        if role in ("human", "user"):
            break
        if role in ("ai", "assistant"):
            continue  # tool-call producer — transparent, same as the streak walks
        if role == "tool":
            entries.append((name, _no_progress_identity(args_by_id.get(str(_tool_call_id(m)), ""), content)))
            if len(entries) >= limit:
                break
            continue
        break  # system or anything unexpected: be conservative
    entries.reverse()
    return entries


def _windowed_identity_count(entries: list[tuple[str | None, str]], identity: str) -> int:
    """How many of ``entries`` share ``identity`` (the caller slices the window)."""
    return sum(1 for _name, entry_identity in entries if entry_identity == identity)


def _detect_tool_name_cycle(
    entries: list[tuple[str | None, str]],
    *,
    rounds: int,
    strong_gate: bool = False,
    min_period: int = _TOOL_CYCLE_MIN_PERIOD,
    max_period: int = _TOOL_CYCLE_MAX_PERIOD,
) -> tuple[int, int] | None:
    """``(period, rounds)`` when the trailing tool-name sequence is the SAME
    ``period``-step pattern repeated ``rounds`` times.

    Novelty gate: the last two repetitions must share same-position identities.
    A legitimate batch over different items re-uses the PATTERN
    (read -> code -> read) with fresh args/results at every position, so it
    never satisfies the gate; a true loop re-reads the same things and re-runs
    the (near-)same steps, so it does. ``strong_gate`` (used before a HARD stop)
    raises the bar to ``max(2, period - 2)`` matches so a two-step batch that
    happens to re-read one shared input can never be aborted.

    Period 1 (one tool hammered repeatedly) is excluded by ``min_period``, and
    so is any pattern made of a SINGLE tool name — consecutive same-tool loops
    are the streak/identity rules' territory (retrying the same failing call
    until it finally succeeds is not a cycle).
    """
    names = [name for name, _identity in entries]
    total = len(names)
    for period in range(min_period, max_period + 1):
        if total < period * rounds:
            continue
        trailing = names[total - period * rounds :]
        base = trailing[:period]
        if any(trailing[i * period : (i + 1) * period] != base for i in range(rounds)):
            continue
        if len(set(base)) < 2:
            continue
        last = entries[total - period :]
        prev = entries[total - 2 * period : total - period]
        matches = sum(1 for i in range(period) if last[i][1] == prev[i][1])
        need = max(2, period - 2) if strong_gate else max(1, period // 2)
        if matches >= need:
            return period, rounds
    return None


# ---------------------------------------------------------------------------
# Corrective hint + salvage assembly
# ---------------------------------------------------------------------------


def _corrective_hint(tool_name: str | None) -> str:
    if tool_name in ("write_file", "edit_file"):
        return (
            "⚠️ 你已连续多次调用 write_file 失败。最可能的原因是要写入的内容过长，导致 content 参数在传输中被截断、"
            "解析后丢失。请立刻改用「分段写入」：先用 write_file 写入文档的第一部分（务必在**同一次**调用里把该部分的"
            "完整文本放进 content 参数），随后用 edit_file 逐段追加后续内容。不要再用空参数或仅 file_path 调用 write_file。"
        )
    if tool_name:
        return (
            f"⚠️ 你已连续多次调用 {tool_name} 失败。请检查工具参数（尤其是必填项是否完整），修正后再试；"
            "不要重复完全相同的错误调用。若无法修正，请换一种方式完成该步骤。"
        )
    return "⚠️ 你已连续多次工具调用失败。请修正参数后再试，不要重复相同的错误调用。"


def _digest_snippets(snippets: list[str], per_cap: int, total_cap: int) -> str:
    out: list[str] = []
    used = 0
    seen: set[str] = set()
    for snip in snippets:
        # The same retrieval answered with the same payload several times is
        # one fact, not N — keep the digest from stacking duplicates.
        key = snip.strip()
        if key in seen:
            continue
        seen.add(key)
        if used >= total_cap:
            out.append("…（其余检索结果已省略）")
            break
        chunk = _cap(snip, min(per_cap, total_cap - used))
        out.append(chunk)
        used += len(chunk)
    return "\n\n---\n\n".join(c for c in out if c)


_EMPTY_KB_MARKERS = (
    "没有找到相关的知识内容",
    '"状态": "无结果"',
    '"status": "no_result"',
)


def _is_empty_kb_result(text: str) -> bool:
    """True for a knowledge-base payload that carries no information.

    The search tool answers a miss with a short JSON stub ({"状态": "无结果", …}).
    Dumping those into the user-facing salvage made three identical "无结果"
    rows look like the model had produced content (2026-10-16 b7e118a6).
    """
    return len(text) < 300 and any(marker in text for marker in _EMPTY_KB_MARKERS)


def assemble_partial_result(messages: list) -> str:
    """Salvage the run's intermediate analysis + retrieved knowledge as markdown.

    Prioritizes the model's own analysis text (AIMessage content = 分析结论);
    appends a trimmed digest of ``search_knowledge_base`` results (检索到的知识).
    Returns an empty string when nothing salvageable exists (caller falls back).

    USER-FACING TEXT — everything collected here ends up verbatim in the
    user-visible "已完成的分析 / 检索到的关键资料" block, so internal plumbing
    must be stripped: the soft-nudge ``[no-progress]`` marker appended to tool
    results (2026-10-16 leak), empty KB stubs, and duplicate payloads are all
    dropped instead of being shown as if they were content.
    """
    analyses: list[str] = []
    kb: list[str] = []
    for m in messages:
        role, name, status, content = _msg_fields(m)
        text = _content_to_text(content).strip()
        if not text:
            continue
        if role in ("ai", "assistant"):
            analyses.append(text)
        elif role == "tool" and name == _KB_TOOL_NAME and status != "error":
            text = _strip_no_progress_hint(text).strip()
            if not text or _is_empty_kb_result(text):
                continue
            kb.append(text)

    parts: list[str] = []
    if analyses:
        parts.append("## 已完成的分析\n\n" + _cap("\n\n".join(analyses), _SALVAGE_ANALYSIS_CAP))
    if kb:
        digest = _digest_snippets(kb, _SALVAGE_KB_PER_CAP, _SALVAGE_KB_TOTAL_CAP)
        if digest:
            parts.append("## 检索到的关键资料（摘要）\n\n" + digest)
    return "\n\n".join(parts).strip()


# ---------------------------------------------------------------------------
# Middleware
# ---------------------------------------------------------------------------


class LinsightToolLoopBreakerMiddleware(AgentMiddleware):
    """Bound same-tool loops — consecutive failures AND consecutive no-progress
    successes (identical output): soft nudge, then hard stop."""

    def __init__(
        self,
        *,
        soft_limit: int = 3,
        hard_limit: int = 8,
        window: int = 24,
        cycle_soft_rounds: int = 3,
        cycle_hard_rounds: int = 5,
        search_soft: int = 12,
        search_hard: int = 24,
        search_window: int = 48,
        is_subagent: bool = False,
    ) -> None:
        super().__init__()
        self.tools = []  # registers no extra tools
        self.soft_limit = max(1, soft_limit)
        self.hard_limit = max(self.soft_limit + 1, hard_limit)
        # Phase-2 knobs: `window` bounds the order-insensitive repeat count;
        # `cycle_*_rounds` bound repeated tool-name patterns.
        self.window = max(4, window)
        self.cycle_soft_rounds = max(2, cycle_soft_rounds)
        self.cycle_hard_rounds = max(self.cycle_soft_rounds + 1, cycle_hard_rounds)
        # Retrieval budget: same search tool called with ever-changing args.
        # Subagents legitimately search more (research is their whole job), so
        # the builder hands them wider defaults.
        self.search_soft = max(2, search_soft)
        self.search_hard = max(self.search_soft + 1, search_hard)
        self.search_window = max(8, search_window)
        self.is_subagent = is_subagent

    @property
    def name(self) -> str:
        # Role-distinct, stable name so the two instances (main vs subagent) never
        # collide in a shared middleware list.
        return f"LinsightToolLoopBreaker{'Sub' if self.is_subagent else 'Main'}"

    async def awrap_tool_call(self, request, handler):
        """SOFT nudge: append a corrective hint once a tool keeps FAILING — or
        keeps returning the identical successful result (no progress)."""
        response = await handler(request)
        # A Command (handoff) or an interrupt (which raises out of ``handler``
        # and never reaches here) passes through untouched.
        if not isinstance(response, ToolMessage):
            return response
        tool_call = request.tool_call or {}
        tool_name = tool_call.get("name")
        base = response.content if isinstance(response.content, str) else _content_to_text(response.content)
        if response.status == "error":
            # This failing result is not yet in state; count prior same-tool failures + 1.
            current = _same_tool_streak(_state_messages(request.state), tool_name) + 1
            if current >= self.soft_limit:
                hint = _corrective_hint(tool_name)
                return response.model_copy(update={"content": f"{base}\n\n{hint}"})
            return response
        # No-progress run: the same tool returning the identical output, run
        # after run (the 2026-10-15 "0" / "[] False" loops all "succeeded").
        run_tool, run_identity, prior = _trailing_no_progress_run(_state_messages(request.state))
        # Args are part of the identity: an edit storm on one file returns the
        # same confirmation text every time but is real progress (see
        # ``_no_progress_identity``).
        identity = _no_progress_identity(
            _args_digest(tool_call.get("args")), response.content
        )
        current = prior + 1 if (run_tool == tool_name and run_identity == identity) else 1
        # Nudge plain-text results only: replacing a multimodal result with a
        # text hint would silently DROP its image blocks from the conversation.
        # Identical multimodal repeats are still counted and bounded by the
        # hard stop.
        updated_content: str | None = None
        if current >= self.soft_limit and isinstance(response.content, str):
            updated_content = f"{base}{_NO_PROGRESS_MARKER}{_no_progress_hint(tool_name)}"

        # ---------------------------------------------------------- phase 2
        # Order-insensitive rules: the consecutive streaks above cannot see an
        # interleaved loop, which is exactly what a "[read x5 -> tweak script]"
        # cycle produces (live run b6014d82, 2026-10-16).
        if isinstance(response.content, str):
            entries = _trailing_tool_entries(_state_messages(request.state))
            entries.append((tool_name, identity))
            windowed = entries[-self.window :]
            repeat_count = _windowed_identity_count(windowed, identity)
            if updated_content is None and repeat_count >= self.soft_limit:
                updated_content = f"{base}{_NO_PROGRESS_MARKER}{_repeat_hint(tool_name, repeat_count)}"
            cycle = _detect_tool_name_cycle(entries, rounds=self.cycle_soft_rounds)
            if cycle is not None:
                period, rounds = cycle
                lesson = updated_content if updated_content is not None else base
                updated_content = f"{lesson}{_CYCLE_MARKER}{_cycle_hint(period, rounds)}"
            # Retrieval budget: the same SEARCH tool called over and over with
            # fresh args — the identity rules cannot see it (each call has a new
            # identity) and the cycle rule needs >= 2 tool names (this is one
            # name repeated, period 1). Live run bdffc2b2, 2026-10-16.
            if tool_name in _SEARCH_TOOL_NAMES:
                search_calls = _windowed_tool_name_count(entries, tool_name, self.search_window)
                if search_calls >= self.search_soft:
                    lesson = updated_content if updated_content is not None else base
                    hint = _search_budget_hint(tool_name, search_calls, is_subagent=self.is_subagent)
                    updated_content = f"{lesson}{_SEARCH_BUDGET_MARKER}{hint}"
        if updated_content is not None:
            return response.model_copy(update={"content": updated_content})
        return response

    def _check_hard_limit(self, state) -> None:
        """Raise ``LinsightToolLoopError`` iff a tool has looped ``hard_limit``
        times in a row — consecutive failures OR consecutive identical-output
        successes — AND the model is STILL trying to call that same tool.

        The second guard is essential: without it, a model that recovered on the
        turn AFTER the streak (a plain text answer, or switching tools) would be
        wrongly aborted because the trailing ToolMessage still shows the loop.
        """
        messages = _state_messages(state)
        last_ai = _last_ai_message(messages)
        pending = _tool_calls_of(last_ai)
        if not pending:
            return  # model produced a final answer / stopped looping — let it complete

        def _still_calling(tool: str | None) -> bool:
            return bool(tool) and any(tc.get("name") == tool for tc in pending)

        run_tool, run_count = _trailing_tool_failure_run(messages)
        if run_tool is not None and run_count >= self.hard_limit and _still_calling(run_tool):
            salvage = assemble_partial_result(messages)
            logger.warning(
                "[linsight-toolloop] '{}' failed {} times consecutively ({}); aborting with "
                "{} chars of salvaged partial result",
                run_tool,
                run_count,
                self.name,
                len(salvage),
            )
            raise LinsightToolLoopError(tool_name=run_tool, count=run_count, partial_result=salvage)

        np_tool, _np_base, np_count = _trailing_no_progress_run(messages)
        if np_tool is not None and np_count >= self.hard_limit and _still_calling(np_tool):
            salvage = assemble_partial_result(messages)
            logger.warning(
                "[linsight-toolloop] '{}' returned identical output {} times consecutively ({}); aborting with "
                "{} chars of salvaged partial result",
                np_tool,
                np_count,
                self.name,
                len(salvage),
            )
            raise LinsightToolLoopError(
                tool_name=np_tool, count=np_count, partial_result=salvage, reason="no_progress"
            )

        # ------------------------------------------------------------ phase 2
        entries = _trailing_tool_entries(messages)
        if not entries:
            return

        # (a) The same (args + result) identity repeated within the window,
        # interleaving included — the shape consecutive streaks cannot count.
        last_name, last_identity = entries[-1]
        if last_name and _still_calling(last_name):
            repeat_count = _windowed_identity_count(entries[-self.window :], last_identity)
            if repeat_count >= self.hard_limit:
                salvage = assemble_partial_result(messages)
                logger.warning(
                    "[linsight-toolloop] '{}' repeated the same args+result {} times within the last "
                    "{} tool calls ({}); aborting with {} chars of salvaged partial result",
                    last_name,
                    repeat_count,
                    self.window,
                    self.name,
                    len(salvage),
                )
                raise LinsightToolLoopError(
                    tool_name=last_name, count=repeat_count, partial_result=salvage, reason="repeat"
                )

        # (b) The trailing tool-name sequence repeating the same pattern; the
        # model must still be continuing the pattern for the abort to be fair.
        cycle = _detect_tool_name_cycle(entries, rounds=self.cycle_hard_rounds, strong_gate=True)
        if cycle is not None:
            period, rounds = cycle
            next_name = entries[-period][0]
            if _still_calling(next_name):
                salvage = assemble_partial_result(messages)
                logger.warning(
                    "[linsight-toolloop] repeated the same {}-step tool cycle {} times ({}); "
                    "aborting with {} chars of salvaged partial result",
                    period,
                    rounds,
                    self.name,
                    len(salvage),
                )
                raise LinsightToolLoopError(
                    tool_name=next_name,
                    count=rounds,
                    period=period,
                    partial_result=salvage,
                    reason="cycle",
                )

        # (c) Retrieval budget: the same SEARCH tool driven far past a useful
        # count with ever-changing args. Invisible to (a) because every query
        # has a fresh identity, and to (b) because a single repeated tool name
        # is a period-1 "cycle" the cycle rule deliberately ignores. Live run
        # bdffc2b2 (2026-10-16): 40+ web_search calls returning the same
        # articles until the user terminated the task.
        for search_name in _SEARCH_TOOL_NAMES:
            if not _still_calling(search_name):
                continue
            search_calls = _windowed_tool_name_count(entries, search_name, self.search_window)
            if search_calls >= self.search_hard:
                salvage = assemble_partial_result(messages)
                logger.warning(
                    "[linsight-toolloop] '{}' reached {} retrieval calls within the last {} tool "
                    "calls and is still being called ({}); aborting with {} chars of salvaged "
                    "partial result",
                    search_name,
                    search_calls,
                    self.search_window,
                    self.name,
                    len(salvage),
                )
                raise LinsightToolLoopError(
                    tool_name=search_name,
                    count=search_calls,
                    partial_result=salvage,
                    reason="search_budget",
                )

    async def aafter_model(self, state, runtime):
        self._check_hard_limit(state)
        return None

    def after_model(self, state, runtime):
        self._check_hard_limit(state)
        return None


def build_tool_loop_breaker_middleware(linsight_conf, *, is_subagent: bool) -> LinsightToolLoopBreakerMiddleware:
    """Construct a middleware instance from ``LinsightConf`` (one per graph)."""
    # Retrieval budgets differ by role: the main agent is supposed to DELEGATE
    # heavy searching (its budget is a convergence trigger), while a research
    # subagent searches as its job and only needs the far-out fail-safe.
    default_search_soft, default_search_hard = (20, 36) if is_subagent else (12, 24)
    return LinsightToolLoopBreakerMiddleware(
        soft_limit=getattr(linsight_conf, "tool_failure_soft_limit", 3),
        hard_limit=getattr(linsight_conf, "tool_failure_hard_limit", 8),
        window=getattr(linsight_conf, "tool_loop_window", 24),
        cycle_soft_rounds=getattr(linsight_conf, "tool_cycle_soft_rounds", 3),
        cycle_hard_rounds=getattr(linsight_conf, "tool_cycle_hard_rounds", 5),
        search_soft=getattr(linsight_conf, "tool_search_soft", default_search_soft),
        search_hard=getattr(linsight_conf, "tool_search_hard", default_search_hard),
        search_window=getattr(linsight_conf, "tool_search_window", 48),
        is_subagent=is_subagent,
    )
