"""Unit tests for LinsightToolLoopBreakerMiddleware (L3) + salvage + L4 label.

Covers the same-tool consecutive-failure breaker:
- soft nudge appended to the error ToolMessage at ``soft_limit``,
- hard stop raising ``LinsightToolLoopError`` (with salvage) at ``hard_limit``,
- the two false-abort guards (model recovered / switched tools),
- streak reset on a success, non-error / interrupt pass-through,
- salvage assembly, and the L4 ``TASK_ABORTED`` classifier label.

``asyncio_mode = auto`` — async tests need no decorator.
"""

from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage, ToolMessage
from langgraph.errors import GraphRecursionError

from bisheng.common.services.llm_error_classifier import ErrorType, label_error
from bisheng.linsight.domain.services.tool_loop_middleware import (
    LinsightToolLoopBreakerMiddleware,
    LinsightToolLoopError,
    assemble_partial_result,
    build_tool_loop_breaker_middleware,
)

WRITE_FILE = "write_file"
KB_TOOL = "search_knowledge_base"


# --------------------------------------------------------------------------- helpers


class _Req:
    """Minimal ToolCallRequest stand-in (middleware only reads tool_call + state)."""

    def __init__(self, tool_call, messages):
        self.tool_call = tool_call
        self.state = {"messages": messages}


def _ai_call(tool=WRITE_FILE, args=None, content="", call_id="tc"):
    return AIMessage(
        content=content,
        tool_calls=[
            {"name": tool, "args": args or {"file_path": "/output/report.md"}, "id": call_id, "type": "tool_call"}
        ],
    )


def _err_tm(tool=WRITE_FILE, content="content: Field required", call_id="tc"):
    return ToolMessage(content=content, tool_call_id=call_id, name=tool, status="error")


def _ok_tm(tool=WRITE_FILE, content="written", call_id="tc"):
    return ToolMessage(content=content, tool_call_id=call_id, name=tool)


def _failure_turns(n, tool=WRITE_FILE):
    """n interleaved (AIMessage tool_call -> error ToolMessage) turns."""
    msgs = []
    for _ in range(n):
        msgs.append(_ai_call(tool))
        msgs.append(_err_tm(tool))
    return msgs


def _mw(*, soft=3, hard=8, is_subagent=False, search_soft=12, search_hard=24, **kwargs):
    return LinsightToolLoopBreakerMiddleware(
        soft_limit=soft,
        hard_limit=hard,
        search_soft=search_soft,
        search_hard=search_hard,
        is_subagent=is_subagent,
        **kwargs,
    )


def _error_handler(tool=WRITE_FILE, content="content: Field required"):
    async def handler(_request):
        return _err_tm(tool, content)

    return handler


# --------------------------------------------------------------------------- soft nudge


async def test_soft_nudge_appended_at_threshold():
    mw = _mw(soft=3, hard=8)
    # 2 prior failures + the current pending call => current streak becomes 3 == soft.
    state = _failure_turns(2) + [_ai_call()]
    req = _Req({"name": WRITE_FILE, "args": {}}, state)
    resp = await mw.awrap_tool_call(req, _error_handler())
    assert "content: Field required" in resp.content
    assert "分段写入" in resp.content  # write_file-specific corrective hint present


async def test_soft_nudge_not_appended_below_threshold():
    mw = _mw(soft=3, hard=8)
    # 1 prior failure + pending => current streak 2 < soft(3): unchanged.
    state = _failure_turns(1) + [_ai_call()]
    req = _Req({"name": WRITE_FILE, "args": {}}, state)
    resp = await mw.awrap_tool_call(req, _error_handler())
    assert resp.content == "content: Field required"


async def test_streak_resets_after_a_success():
    mw = _mw(soft=3, hard=8)
    # A success breaks the run; a fresh failure is streak 1, no nudge.
    state = _failure_turns(2) + [_ai_call(), _ok_tm(), _ai_call()]
    req = _Req({"name": WRITE_FILE, "args": {}}, state)
    resp = await mw.awrap_tool_call(req, _error_handler())
    assert resp.content == "content: Field required"


async def test_success_response_passes_through_untouched():
    mw = _mw()
    state = _failure_turns(5) + [_ai_call()]
    req = _Req({"name": WRITE_FILE, "args": {}}, state)

    async def ok_handler(_request):
        return _ok_tm()

    resp = await mw.awrap_tool_call(req, ok_handler)
    assert resp.status != "error"
    assert resp.content == "written"


async def test_interrupt_like_exception_propagates():
    """ask_user's interrupt() raises out of the handler — must not be swallowed."""
    mw = _mw()
    req = _Req({"name": "ask_user", "args": {}}, [])

    async def raising(_request):
        raise RuntimeError("interrupt-like control flow")

    with pytest.raises(RuntimeError):
        await mw.awrap_tool_call(req, raising)


# --------------------------------------------------------------------------- hard stop


async def test_hard_stop_raises_with_salvage_at_limit():
    mw = _mw(soft=3, hard=8)
    analysis = "核心结论：二级市场存在结构性机会，建议关注高景气板块。"
    kb = ToolMessage(content="券商研报：某行业景气度持续上行", tool_call_id="k", name=KB_TOOL)
    messages = [AIMessage(content=analysis), kb] + _failure_turns(8) + [_ai_call()]
    with pytest.raises(LinsightToolLoopError) as ei:
        await mw.aafter_model({"messages": messages}, None)
    assert ei.value.tool_name == WRITE_FILE
    assert ei.value.count >= 8
    assert "核心结论" in ei.value.partial_result  # analysis salvaged
    assert "券商研报" in ei.value.partial_result  # retrieved knowledge salvaged


async def test_no_raise_below_hard_limit():
    mw = _mw(soft=3, hard=8)
    messages = _failure_turns(7) + [_ai_call()]
    assert await mw.aafter_model({"messages": messages}, None) is None


async def test_no_raise_when_model_recovered_with_text_answer():
    """After the streak the model gave a plain answer (no tool call) — must NOT abort."""
    mw = _mw(soft=3, hard=8)
    messages = _failure_turns(8) + [AIMessage(content="这是最终答复", tool_calls=[])]
    assert await mw.aafter_model({"messages": messages}, None) is None


async def test_no_raise_when_model_switched_tools():
    mw = _mw(soft=3, hard=8)
    messages = _failure_turns(8) + [_ai_call(tool="edit_file")]
    assert await mw.aafter_model({"messages": messages}, None) is None


# --------------------------------------------------------------------------- no-progress loop
# The mirror image of the failure loop: every call "succeeds" while returning
# the IDENTICAL output (2026-10-15: 15+ ``os.walk('/root/.cache')`` scans each
# printing ``0``; existence checks printing ``[] / False``) — the error-based
# counter never moves, so the output-identity streak is what bounds it.

NP_TOOL = "bisheng_code_interpreter"
NP_OUT = '{"results": [], "stdout": ["0\\n"]}'
NP_ARGS = {"file_path": "/output/report.md"}  # mirrors _ai_call's default args


def _ok_np(n, *, tool=NP_TOOL, content=NP_OUT):
    """n interleaved (AIMessage tool_call -> identical success ToolMessage) turns."""
    msgs = []
    for _ in range(n):
        msgs.append(_ai_call(tool))
        msgs.append(_ok_tm(tool, content))
    return msgs


async def test_no_progress_soft_nudge_at_threshold():
    mw = _mw(soft=3, hard=8)
    state = [*_ok_np(2), _ai_call(NP_TOOL)]
    req = _Req({"name": NP_TOOL, "args": NP_ARGS}, state)

    async def handler(_request):
        return _ok_tm(NP_TOOL, NP_OUT)

    resp = await mw.awrap_tool_call(req, handler)
    assert NP_OUT in resp.content  # base content preserved
    assert "[no-progress]" in resp.content  # nudge appended at streak == soft


async def test_no_progress_not_nudged_below_threshold():
    mw = _mw(soft=3, hard=8)
    state = [*_ok_np(1), _ai_call(NP_TOOL)]
    req = _Req({"name": NP_TOOL, "args": NP_ARGS}, state)

    async def handler(_request):
        return _ok_tm(NP_TOOL, NP_OUT)

    resp = await mw.awrap_tool_call(req, handler)
    assert resp.content == NP_OUT


async def test_no_progress_streak_resets_on_different_output():
    mw = _mw(soft=3, hard=8)
    state = [*_ok_np(2), _ai_call(NP_TOOL)]
    req = _Req({"name": NP_TOOL, "args": NP_ARGS}, state)

    async def handler(_request):
        return _ok_tm(NP_TOOL, "something new")

    resp = await mw.awrap_tool_call(req, handler)
    assert resp.content == "something new"
    assert "[no-progress]" not in resp.content


async def test_no_progress_nudge_does_not_reset_the_streak():
    """A previous nudge is stripped before comparing, so the count continues."""
    mw = _mw(soft=3, hard=8)
    nudged = _ok_tm(NP_TOOL, NP_OUT).model_copy(
        update={"content": f"{NP_OUT}\n\n[no-progress] ⚠️ 换一种方法"}
    )
    state = [_ai_call(NP_TOOL), _ok_tm(NP_TOOL, NP_OUT), _ai_call(NP_TOOL), nudged, _ai_call(NP_TOOL)]
    req = _Req({"name": NP_TOOL, "args": NP_ARGS}, state)

    async def handler(_request):
        return _ok_tm(NP_TOOL, NP_OUT)

    resp = await mw.awrap_tool_call(req, handler)
    # prior run (with one already-nudged member) + current == 3 → nudged again
    assert "[no-progress]" in resp.content


async def test_no_progress_hard_stop_raises_with_reason_and_salvage():
    mw = _mw(soft=3, hard=8)
    analysis = "分析：已从工作区拿到关键数据。"
    messages = [AIMessage(content=analysis), *_ok_np(8), _ai_call(NP_TOOL)]
    with pytest.raises(LinsightToolLoopError) as ei:
        await mw.aafter_model({"messages": messages}, None)
    assert ei.value.reason == "no_progress"
    assert ei.value.tool_name == NP_TOOL
    assert ei.value.count >= 8
    assert "分析" in ei.value.partial_result


async def test_no_progress_no_raise_below_hard_limit():
    mw = _mw(soft=3, hard=8)
    messages = [*_ok_np(7), _ai_call(NP_TOOL)]
    assert await mw.aafter_model({"messages": messages}, None) is None


async def test_no_progress_no_raise_when_model_recovered():
    mw = _mw(soft=3, hard=8)
    messages = [*_ok_np(8), AIMessage(content="这是最终答复", tool_calls=[])]
    assert await mw.aafter_model({"messages": messages}, None) is None


async def test_no_progress_no_raise_when_model_switched_tools():
    mw = _mw(soft=3, hard=8)
    messages = [*_ok_np(8), _ai_call(tool="edit_file")]
    assert await mw.aafter_model({"messages": messages}, None) is None


EDIT_CONFIRM = "Successfully replaced 1 instance(s) of the string in '/output/report.md'"


def _edit_turns(n, *, same_args=False):
    """n interleaved edit_file turns whose success text is IDENTICAL.

    Real runs give every call a unique id; ``same_args`` flips between distinct
    edits (each replaces a different string — real progress) and one repeated
    edit (a true no-op loop).
    """
    msgs = []
    for i in range(n):
        args = {
            "file_path": "/output/report.md",
            "old_string": "old" if same_args else f"old-{i}",
            "new_string": "new" if same_args else f"new-{i}",
        }
        msgs.append(_ai_call("edit_file", args=args, call_id=f"call_{i}"))
        msgs.append(_ok_tm("edit_file", EDIT_CONFIRM, call_id=f"call_{i}"))
    return msgs


async def test_sequential_edits_with_identical_confirmation_are_not_no_progress():
    """2026-10-16 (b7e118a6): a report edited section by section.

    ``edit_file`` answers every successful edit with the same confirmation
    string, so a result-only identity read 8 distinct edits as "8 identical
    outputs" and aborted two healthy tasks. Arguments are now part of the
    identity: different edits must NOT trip the breaker at all.
    """
    mw = _mw(soft=3, hard=8)
    next_call = _ai_call(
        "edit_file",
        args={"file_path": "/output/report.md", "old_string": "old-8", "new_string": "new-8"},
        call_id="call_8",
    )

    # Hard stop must not fire while the pending call is yet another edit.
    assert await mw.aafter_model({"messages": [*_edit_turns(8), next_call]}, None) is None

    # …and the soft nudge must not fire either.
    state = [
        *_edit_turns(3),
        _ai_call(
            "edit_file",
            args={"file_path": "/output/report.md", "old_string": "old-x", "new_string": "new-x"},
            call_id="call_x",
        ),
    ]
    req = _Req(state[-1].tool_calls[0], state)

    async def handler(_request):
        return _ok_tm("edit_file", EDIT_CONFIRM, call_id="call_x")

    resp = await mw.awrap_tool_call(req, handler)
    assert resp.content == EDIT_CONFIRM
    assert "[no-progress]" not in resp.content


async def test_repeating_the_same_edit_still_hard_stops():
    """The flip side: the SAME edit (same args) answered with the same
    confirmation 8 times in a row is a genuine no-op loop and still aborts."""
    mw = _mw(soft=3, hard=8)
    messages = [
        *_edit_turns(8, same_args=True),
        _ai_call(
            "edit_file",
            args={"file_path": "/output/report.md", "old_string": "old", "new_string": "new"},
            call_id="call_8",
        ),
    ]
    with pytest.raises(LinsightToolLoopError) as ei:
        await mw.aafter_model({"messages": messages}, None)
    assert ei.value.reason == "no_progress"
    assert ei.value.tool_name == "edit_file"


async def test_failure_abort_keeps_its_own_reason():
    mw = _mw(soft=3, hard=8)
    messages = [*_failure_turns(8), _ai_call()]
    with pytest.raises(LinsightToolLoopError) as ei:
        await mw.aafter_model({"messages": messages}, None)
    assert ei.value.reason == "failure"


def _img_tm(b64: str, tool=NP_TOOL):
    """A read_file-like multimodal result: one image block, no text."""
    return ToolMessage(
        content=[{"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}}],
        tool_call_id="tc",
        name=tool,
    )


async def test_no_progress_ignores_distinct_multimodal_results():
    """Different images are different information — not a no-progress loop.

    2026-10-15: the ppt turn read slide-01..08.jpg for visual QA; flattening
    image blocks to "" made the breaker abort it as "8 identical outputs".
    """
    mw = _mw(soft=3, hard=8)
    msgs = []
    for i in range(8):
        msgs.append(_ai_call(NP_TOOL))
        msgs.append(_img_tm(f"AAA{i}"))
    msgs.append(_ai_call(NP_TOOL))
    assert await mw.aafter_model({"messages": msgs}, None) is None

    state = [_ai_call(NP_TOOL), _img_tm("AAA0"), _ai_call(NP_TOOL), _img_tm("AAA1"), _ai_call(NP_TOOL)]
    req = _Req({"name": NP_TOOL, "args": NP_ARGS}, state)

    async def handler(_request):
        return _img_tm("AAA2")

    resp = await mw.awrap_tool_call(req, handler)
    assert isinstance(resp.content, list)  # untouched, no nudge


async def test_no_progress_identical_multimodal_still_hard_stops():
    mw = _mw(soft=3, hard=8)
    msgs = []
    for _ in range(8):
        msgs.append(_ai_call(NP_TOOL))
        msgs.append(_img_tm("SAME"))
    msgs.append(_ai_call(NP_TOOL))
    with pytest.raises(LinsightToolLoopError) as ei:
        await mw.aafter_model({"messages": msgs}, None)
    assert ei.value.reason == "no_progress"


async def test_multimodal_result_never_rewritten_by_soft_nudge():
    """The soft nudge must not replace image blocks with a text hint (that
    would silently drop the image from the conversation)."""
    mw = _mw(soft=3, hard=8)
    state = [_ai_call(NP_TOOL), _img_tm("S"), _ai_call(NP_TOOL), _img_tm("S"), _ai_call(NP_TOOL)]
    req = _Req({"name": NP_TOOL, "args": NP_ARGS}, state)

    async def handler(_request):
        return _img_tm("S")

    resp = await mw.awrap_tool_call(req, handler)
    # streak == 3 >= soft, but multimodal → returned untouched
    assert isinstance(resp.content, list)
    assert resp.content[0]["type"] == "image_url"


# --------------------------------------------------------------------------- salvage assembly


def test_assemble_partial_result_prioritizes_analysis_and_digests_kb():
    messages = [
        AIMessage(content="分析：A 股估值处于历史低位"),
        ToolMessage(content="研报正文：行业A 需求回暖", tool_call_id="k1", name=KB_TOOL),
        _ai_call(),  # empty-content tool-call producer — must be ignored
        _err_tm(),
    ]
    out = assemble_partial_result(messages)
    assert "## 已完成的分析" in out
    assert "A 股估值处于历史低位" in out
    assert "## 检索到的关键资料（摘要）" in out
    assert "行业A 需求回暖" in out


def test_assemble_partial_result_empty_when_nothing_salvageable():
    # Only failed tool calls, no analysis text and no KB results.
    assert assemble_partial_result(_failure_turns(3)) == ""


def test_assemble_partial_result_strips_internal_nudges_and_empty_kb_stubs():
    """2026-10-16 (b7e118a6): the user-facing salvage leaked internal plumbing —
    the soft-nudge ``[no-progress]`` hint and three identical "无结果" KB stubs
    were printed as if they were content."""
    nudged_kb = ToolMessage(
        content=(
            '{"状态": "无结果", "错误信息":"没有找到相关的知识内容"}'
            "\n\n[no-progress] ⚠️ 你已连续多次调用 search_knowledge_base 并拿到了完全相同的结果"
        ),
        tool_call_id="k1",
        name=KB_TOOL,
    )
    useful_kb = ToolMessage(
        content="研报正文：行业A 需求回暖", tool_call_id="k2", name=KB_TOOL
    )
    messages = [AIMessage(content="分析：已确认三个要点"), nudged_kb, useful_kb]

    out = assemble_partial_result(messages)

    assert "已确认三个要点" in out
    assert "需求回暖" in out
    assert "[no-progress]" not in out  # internal nudge stripped
    assert "无结果" not in out  # empty KB stub dropped entirely


def test_assemble_partial_result_dedupes_identical_kb_payloads():
    same = ToolMessage(content="研报正文：同一份资料", tool_call_id="k1", name=KB_TOOL)
    dup = ToolMessage(content="研报正文：同一份资料", tool_call_id="k2", name=KB_TOOL)

    out = assemble_partial_result([same, dup])

    assert out.count("同一份资料") == 1


# --------------------------------------------------------------------------- factory + L4 label


def test_build_from_conf_reads_thresholds():
    conf = SimpleNamespace(tool_failure_soft_limit=2, tool_failure_hard_limit=5)
    mw = build_tool_loop_breaker_middleware(conf, is_subagent=True)
    assert mw.soft_limit == 2
    assert mw.hard_limit == 5
    assert mw.is_subagent is True
    assert mw.name == "LinsightToolLoopBreakerSub"


def test_hard_limit_kept_above_soft():
    # Guard: hard must stay strictly above soft even if misconfigured equal.
    mw = LinsightToolLoopBreakerMiddleware(soft_limit=5, hard_limit=3)
    assert mw.hard_limit > mw.soft_limit


# --------------------------------------------------------------------------- phase 2
# Order-insensitive cycle detection (2026-10-16, live run b6014d82): the
# consecutive-streak rules cannot see "[read x5 -> tweak the build script]"
# loops — every read broke the previous read-streak and every code call carried
# slightly different args, so neither counter ever advanced.

READ_TOOL = "read_file"
CODE_TOOL = "bisheng_code_interpreter"


def _cycle_round(round_index: int, *, identical: bool) -> list:
    """One ``[read x5 -> code]`` round.

    ``identical=True`` repeats the same five reads and the same script every
    round (a true polish loop); ``identical=False`` gives every call fresh
    args/results (a legitimate batch over different items). Call ids are unique
    per round so ``_tool_args_by_id`` maps each result to its own arguments.
    """
    msgs: list = []
    for i in range(5):
        suffix = "" if identical else f"round{round_index}-"
        args = {"file_path": f"/output/{suffix}page-{i}.jpg"}
        content = f"img:{i}" if identical else f"img:{round_index}-{i}"
        call_id = f"r{round_index}-{i}"
        msgs.append(_ai_call(READ_TOOL, args, call_id=call_id))
        msgs.append(_ok_tm(READ_TOOL, content, call_id=call_id))
    code = {"python_code": "render()"} if identical else {"python_code": f"render({round_index})"}
    call_id = f"c{round_index}"
    msgs.append(_ai_call(CODE_TOOL, code, call_id=call_id))
    msgs.append(_ok_tm(CODE_TOOL, "0", call_id=call_id))
    return msgs


async def test_tool_name_cycle_hard_stops_when_the_pattern_repeats():
    mw = _mw()  # cycle_hard_rounds default = 5, period cap = 6
    analysis = "分析：正在生成报告文档。"
    messages = [AIMessage(content=analysis)]
    for round_index in range(5):
        messages += _cycle_round(round_index, identical=True)
    # The model is starting another round — the abort must be fair.
    messages.append(_ai_call(READ_TOOL, {"file_path": "/output/page-0.jpg"}, call_id="r-next"))

    with pytest.raises(LinsightToolLoopError) as ei:
        await mw.aafter_model({"messages": messages}, None)

    assert ei.value.reason == "cycle"
    assert ei.value.period == 6
    assert ei.value.count == 5
    assert ei.value.tool_name == READ_TOOL
    assert "分析" in ei.value.partial_result


async def test_tool_name_cycle_not_raised_for_a_batch_over_distinct_items():
    mw = _mw()
    messages: list = []
    for round_index in range(5):
        messages += _cycle_round(round_index, identical=False)
    messages.append(_ai_call(READ_TOOL, {"file_path": "/output/round5-page-0.jpg"}, call_id="r-next"))

    # Same SHAPE five times, but no position ever repeats an identity: a real
    # batch, not a loop.
    assert await mw.aafter_model({"messages": messages}, None) is None


async def test_windowed_repeat_hard_stops_across_interleaving():
    mw = _mw()
    messages = [AIMessage(content="分析：核对缓存文件。")]
    # The same read repeated 8x, every occurrence separated by a DIFFERENT
    # filler tool -> the consecutive streak never moves, the windowed count does.
    repeated_args = {"file_path": "/cache/x.json"}
    entries: list = []
    for i in range(7):
        entries.append(_ai_call(f"filler_{i}", {"query": f"q{i}"}, call_id=f"f{i}"))
        entries.append(_ok_tm(f"filler_{i}", f"hit:{i}", call_id=f"f{i}"))
        entries.append(_ai_call(READ_TOOL, repeated_args, call_id=f"a{i}"))
        entries.append(_ok_tm(READ_TOOL, "{}", call_id=f"a{i}"))
    entries.append(_ai_call(READ_TOOL, repeated_args, call_id="a-last"))
    entries.append(_ok_tm(READ_TOOL, "{}", call_id="a-last"))
    messages += entries
    messages.append(_ai_call(READ_TOOL, repeated_args, call_id="a-next"))

    with pytest.raises(LinsightToolLoopError) as ei:
        await mw.aafter_model({"messages": messages}, None)

    assert ei.value.reason == "repeat"
    assert ei.value.tool_name == READ_TOOL
    assert ei.value.count >= 8


async def test_windowed_repeat_soft_nudge_without_a_consecutive_streak():
    mw = _mw(soft=3, hard=8)
    args = {"file_path": "/cache/x.json"}
    state = [
        _ai_call(READ_TOOL, args, call_id="p1"),
        _ok_tm(READ_TOOL, "{}", call_id="p1"),
        _ai_call("grep", {"pattern": "q1"}, call_id="g1"),
        _ok_tm("grep", "hit1", call_id="g1"),
        _ai_call(READ_TOOL, args, call_id="p2"),
        _ok_tm(READ_TOOL, "{}", call_id="p2"),
    ]
    req = _Req({"name": READ_TOOL, "args": args, "id": "p3"}, state)

    async def handler(_request):
        return _ok_tm(READ_TOOL, "{}", call_id="p3")

    resp = await mw.awrap_tool_call(req, handler)
    assert "[no-progress]" in resp.content
    assert "不会带来新信息" in resp.content  # the windowed-repeat wording


async def test_cycle_soft_nudge_appended_on_the_third_round():
    mw = _mw()
    state: list = [AIMessage(content="分析")]
    state += _cycle_round(0, identical=True)
    state += _cycle_round(1, identical=True)
    state += _cycle_round(2, identical=True)[:-2]  # third round minus its code call
    req = _Req({"name": CODE_TOOL, "args": {"python_code": "render()"}, "id": "c2"}, state)

    async def handler(_request):
        return _ok_tm(CODE_TOOL, "0", call_id="c2")

    resp = await mw.awrap_tool_call(req, handler)
    assert "[cycle]" in resp.content
    assert "同一套" in resp.content


def test_build_from_conf_reads_cycle_knobs():
    conf = SimpleNamespace(tool_loop_window=12, tool_cycle_soft_rounds=2, tool_cycle_hard_rounds=4)
    mw = build_tool_loop_breaker_middleware(conf, is_subagent=False)
    assert mw.window == 12
    assert mw.cycle_soft_rounds == 2
    assert mw.cycle_hard_rounds == 4


def test_label_error_graph_recursion_is_task_aborted():
    assert label_error(GraphRecursionError("Recursion limit of 200 reached")) == ErrorType.TASK_ABORTED


def test_label_error_tool_loop_is_task_aborted():
    assert label_error(LinsightToolLoopError(tool_name=WRITE_FILE, count=8)) == ErrorType.TASK_ABORTED


# --------------------------------------------------------------------------- integration
# Verifies the composition that unit tests can't: a real langchain agent graph
# where the model loops on a failing tool must RAISE LinsightToolLoopError out of
# ``ainvoke`` (i.e. the aafter_model raise actually propagates through the graph,
# beating the recursion limit) — the load-bearing assumption of the whole design.


async def test_hard_stop_propagates_through_real_agent_graph():
    from langchain.agents import create_agent
    from langchain_core.language_models import BaseChatModel
    from langchain_core.messages import HumanMessage
    from langchain_core.outputs import ChatGeneration, ChatResult
    from langchain_core.tools import tool
    from pydantic import BaseModel

    class _FailSchema(BaseModel):
        required_field: str

    @tool(args_schema=_FailSchema)
    def failing_tool(required_field: str) -> str:
        """A tool with a required field the fake model always omits."""
        return "ok"

    class _AlwaysCallsFailingTool(BaseChatModel):
        """Fake model that every turn calls failing_tool WITHOUT the required arg,
        reproducing the real ``content: Field required`` retry loop."""

        def bind_tools(self, tools, **kwargs):
            return self

        def _generate(self, messages, stop=None, run_manager=None, **kwargs):
            ai = AIMessage(
                content="",
                # unique id per turn (messages grow each round) so tool_call ids never collide
                tool_calls=[{"name": "failing_tool", "args": {}, "id": f"call_{len(messages)}", "type": "tool_call"}],
            )
            return ChatResult(generations=[ChatGeneration(message=ai)])

        async def _agenerate(self, messages, stop=None, run_manager=None, **kwargs):
            return self._generate(messages, stop, run_manager, **kwargs)

        @property
        def _llm_type(self):
            return "fake-always-fails"

    agent = create_agent(
        _AlwaysCallsFailingTool(),
        tools=[failing_tool],
        middleware=[LinsightToolLoopBreakerMiddleware(soft_limit=2, hard_limit=3)],
    )

    with pytest.raises(LinsightToolLoopError) as ei:
        await agent.ainvoke({"messages": [HumanMessage(content="go")]})
    assert ei.value.tool_name == "failing_tool"
    assert ei.value.count >= 3


async def test_no_progress_hard_stop_propagates_through_real_agent_graph():
    """The 2026-10-15 mirror-image loop must ALSO raise through a real graph:
    every tool call succeeds and returns the identical output, so the abort can
    only come from the content-identity streak."""
    from langchain.agents import create_agent
    from langchain_core.language_models import BaseChatModel
    from langchain_core.messages import HumanMessage
    from langchain_core.outputs import ChatGeneration, ChatResult
    from langchain_core.tools import tool

    @tool
    def echo_zero() -> str:
        """Always returns the same output — a stand-in for a stalled scan."""
        return "0"

    class _AlwaysCallsEcho(BaseChatModel):
        """Fake model that every turn calls echo_zero again, forever."""

        def bind_tools(self, tools, **kwargs):
            return self

        def _generate(self, messages, stop=None, run_manager=None, **kwargs):
            ai = AIMessage(
                content="",
                # unique id per turn (messages grow each round) so ids never collide
                tool_calls=[
                    {"name": "echo_zero", "args": {}, "id": f"call_{len(messages)}", "type": "tool_call"}
                ],
            )
            return ChatResult(generations=[ChatGeneration(message=ai)])

        async def _agenerate(self, messages, stop=None, run_manager=None, **kwargs):
            return self._generate(messages, stop, run_manager, **kwargs)

        @property
        def _llm_type(self):
            return "fake-always-echoes"

    agent = create_agent(
        _AlwaysCallsEcho(),
        tools=[echo_zero],
        middleware=[LinsightToolLoopBreakerMiddleware(soft_limit=2, hard_limit=3)],
    )

    with pytest.raises(LinsightToolLoopError) as ei:
        await agent.ainvoke({"messages": [HumanMessage(content="go")]})
    assert ei.value.reason == "no_progress"
    assert ei.value.tool_name == "echo_zero"
    assert ei.value.count >= 3


# --------------------------------------------------------------- retrieval budget
# Live run bdffc2b2 (2026-10-16): the main agent issued 40+ web_search calls with
# slightly different keywords; results kept collapsing to the same handful of
# articles until the user terminated the task. Every call had a fresh identity
# (so the repeat rules never matched) and only one tool name repeated (so the
# cycle rule — which needs >= 2 names — never fired). Hence a dedicated budget.

SEARCH = "web_search"


def _search_turns(n, tool=SEARCH):
    """n SEARCH turns: every query AND every result DIFFERENT (the shape that
    slipped past every identity rule)."""
    msgs = []
    for i in range(n):
        msgs.append(_ai_call(tool, args={"query": f"央行 支付体系 信用卡 数据 {i}"}, call_id=f"sc{i}"))
        msgs.append(_ok_tm(tool, content=f'[{{"id": "r{i}", "title": "article {i}"}}]', call_id=f"sc{i}"))
    return msgs


async def test_search_budget_soft_nudge_at_threshold():
    mw = _mw(search_soft=4, search_hard=10)
    state = [*_search_turns(3), _ai_call(SEARCH, args={"query": "又换一个关键词"})]
    req = _Req({"name": SEARCH, "args": {"query": "又换一个关键词"}}, state)

    async def handler(_request):
        return _ok_tm(SEARCH, content='[{"id": "r9", "title": "another"}]')

    resp = await mw.awrap_tool_call(req, handler)
    assert "[search-budget]" in resp.content
    assert "委派资料研究员" in resp.content


async def test_search_budget_soft_nudge_below_threshold_is_absent():
    mw = _mw(search_soft=4, search_hard=10)
    state = [*_search_turns(2), _ai_call(SEARCH, args={"query": "q"})]
    req = _Req({"name": SEARCH, "args": {"query": "q"}}, state)

    async def handler(_request):
        return _ok_tm(SEARCH, content='[{"id": "r1"}]')

    resp = await mw.awrap_tool_call(req, handler)
    assert "[search-budget]" not in resp.content


async def test_search_budget_never_touches_other_tools():
    """read_file with distinct paths is legitimate work; only retrieval tools
    carry the budget."""
    mw = _mw(search_soft=4, search_hard=10)
    msgs = []
    for i in range(6):
        msgs.append(_ai_call("read_file", args={"file_path": f"/output/x{i}.md"}, call_id=f"rc{i}"))
        msgs.append(_ok_tm("read_file", content=f"file {i}", call_id=f"rc{i}"))
    msgs.append(_ai_call("read_file", args={"file_path": "/output/x9.md"}))
    req = _Req({"name": "read_file", "args": {"file_path": "/output/x9.md"}}, msgs)

    async def handler(_request):
        return _ok_tm("read_file", content="file 9")

    resp = await mw.awrap_tool_call(req, handler)
    assert "[search-budget]" not in resp.content
    assert await mw.aafter_model({"messages": msgs}, None) is None


async def test_search_budget_hard_stops_when_still_searching():
    mw = _mw(search_soft=3, search_hard=6)
    messages = [*_search_turns(6), _ai_call(SEARCH, args={"query": "yet another keyword"})]
    with pytest.raises(LinsightToolLoopError) as ei:
        await mw.aafter_model({"messages": messages}, None)
    assert ei.value.reason == "search_budget"
    assert ei.value.tool_name == SEARCH
    assert ei.value.count >= 6


async def test_search_budget_hard_stop_spares_a_recovered_model():
    """Moving on to writing after the budget is a recovery — no abort."""
    mw = _mw(search_soft=3, search_hard=6)
    messages = [*_search_turns(6), _ai_call("write_file", args={"file_path": "/output/a.md"})]
    assert await mw.aafter_model({"messages": messages}, None) is None


async def test_search_budget_window_counts_interleaved_calls():
    """The count is windowed, not a consecutive streak: interleaved writes do
    not reset it (the loop shape of bdffc2b2 included todo updates between
    searches)."""
    mw = _mw(search_soft=4, search_hard=20)
    msgs = []
    for i in range(3):
        msgs.append(_ai_call(SEARCH, args={"query": f"q{i}"}, call_id=f"sq{i}"))
        msgs.append(_ok_tm(SEARCH, content=f'[{{"id": "r{i}"}}]', call_id=f"sq{i}"))
        msgs.append(_ai_call("write_todos", args={"todos": []}, call_id=f"wt{i}"))
        msgs.append(_ok_tm("write_todos", content="updated", call_id=f"wt{i}"))
    msgs.append(_ai_call(SEARCH, args={"query": "q3"}))
    # 3 prior searches + this pending one => 4 in the window => nudge fires.
    state = msgs
    req = _Req({"name": SEARCH, "args": {"query": "q3"}}, state)

    async def handler(_request):
        return _ok_tm(SEARCH, content='[{"id": "r3"}]')

    resp = await mw.awrap_tool_call(req, handler)
    assert "[search-budget]" in resp.content
