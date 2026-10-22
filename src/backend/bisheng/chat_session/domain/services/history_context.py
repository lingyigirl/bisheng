"""按 Token 预算从完整轮次和分层摘要中组装历史上下文。"""

# ruff: noqa: RUF002,RUF003 -- 中文 docstring 和注释使用全角标点。

from collections.abc import Callable
from dataclasses import dataclass, field


@dataclass(frozen=True)
class HistoryTurn:
    turn_no: int
    question_text: str
    answer_text: str
    token_count: int = 0


@dataclass(frozen=True)
class HistorySummary:
    start_turn_no: int
    end_turn_no: int
    depth: int
    summary: str
    token_count: int
    source_token_count: int
    context_token_count: int = 0


@dataclass
class HistorySelection:
    turns: list[HistoryTurn] = field(default_factory=list)
    summaries: list[HistorySummary] = field(default_factory=list)


def render_turn(turn: HistoryTurn) -> str:
    return f"Human: {turn.question_text}\nAI: {turn.answer_text}"


def render_summary(summary: HistorySummary) -> str:
    return f"历史摘要({summary.start_turn_no}-{summary.end_turn_no})\n{summary.summary}"


def _summary_is_usable(summary: HistorySummary) -> bool:
    return bool(summary.summary.strip()) and summary.token_count < summary.source_token_count


def count_text_tokens(text: str | None) -> int:
    """使用统一的 cl100k_base 估算文本 Token。"""
    if not text:
        return 0
    try:
        import tiktoken

        return len(tiktoken.get_encoding("cl100k_base").encode(text))
    except Exception:
        return max(1, len(text) // 2)


def assemble_history(
    *,
    turns: list[HistoryTurn],
    summaries: list[HistorySummary],
    max_tokens: int,
    count_tokens: Callable[[str], int],
) -> tuple[str, HistorySelection]:
    """选择最近完整原文，预算不足时把最远原文降级为已有摘要。"""
    if max_tokens <= 0:
        return "", HistorySelection()

    ordered_turns = sorted(turns, key=lambda item: item.turn_no)
    summaries_by_turn = {
        item.start_turn_no: item
        for item in summaries
        if item.depth == 0 and item.start_turn_no == item.end_turn_no and _summary_is_usable(item)
    }
    selected = HistorySelection()
    used = 0
    boundary = ordered_turns[-1].turn_no + 1 if ordered_turns else 0

    for turn in reversed(ordered_turns):
        raw = render_turn(turn)
        raw_tokens = turn.token_count or count_tokens(raw)
        if used + raw_tokens <= max_tokens:
            selected.turns.append(turn)
            used += raw_tokens
            boundary = turn.turn_no
            continue

        d0 = summaries_by_turn.get(turn.turn_no)
        if d0 is None:
            break
        rendered = render_summary(d0)
        summary_tokens = d0.context_token_count or count_tokens(rendered)
        if summary_tokens >= raw_tokens or used + summary_tokens > max_tokens:
            break
        selected.summaries.append(d0)
        used += summary_tokens
        boundary = turn.turn_no

    # 更早的区间摘要只覆盖已经选中的最远轮次之前，避免父子或原文重叠。
    older_summaries = [item for item in summaries if item.end_turn_no < boundary and _summary_is_usable(item)]
    older_summaries.sort(key=lambda item: (item.end_turn_no, item.depth), reverse=True)
    covered_until = boundary - 1
    for summary in older_summaries:
        if summary.end_turn_no > covered_until:
            continue
        rendered = render_summary(summary)
        summary_tokens = summary.context_token_count or count_tokens(rendered)
        if used + summary_tokens > max_tokens:
            break
        selected.summaries.append(summary)
        used += summary_tokens
        covered_until = summary.start_turn_no - 1

    selected.turns.sort(key=lambda item: item.turn_no)
    selected.summaries.sort(key=lambda item: (item.start_turn_no, item.depth))
    parts = [render_summary(item) for item in selected.summaries]
    parts.extend(render_turn(item) for item in selected.turns)
    return "\n\n".join(parts), selected


def plan_summary_merge(
    summaries: list[HistorySummary],
    *,
    depth: int,
    group_size: int,
) -> list[HistorySummary] | None:
    """找出最早一组相邻、连续且未重叠的摘要。"""
    if group_size < 2:
        return None
    rows = sorted(
        (item for item in summaries if item.depth == depth and _summary_is_usable(item)),
        key=lambda item: (item.start_turn_no, item.end_turn_no),
    )
    for start in range(len(rows) - group_size + 1):
        group = rows[start : start + group_size]
        if all(group[index].end_turn_no + 1 == group[index + 1].start_turn_no for index in range(len(group) - 1)):
            return group
    return None
