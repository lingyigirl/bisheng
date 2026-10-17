"""Unit tests for the malformed tool-call guard in LinsightModelResilienceMiddleware.

A model that emits a tool call whose arguments never parse surfaces it ONLY as
``invalid_tool_calls`` — the call never reaches the tool node and no ToolMessage
is produced. The 2026-10-14 incident: ``ask_user(INVALID)``; the clarify turn
never parked and the task closed COMPLETED with just the model's preamble
(``我先确认几个关键点…``) plus the previous turn's files. The guard nudges a
bounded number of times so a recoverable model re-emits legal JSON; an
unresolvable run is left to task_exec's completion guard, which fails the round
instead of faking a direct answer.

``asyncio_mode = auto`` — async tests need no decorator.
"""

from langchain.agents.middleware.types import ModelRequest, ModelResponse
from langchain_core.messages import AIMessage, HumanMessage

from bisheng.linsight.domain.services.resilience_middleware import (
    _INVALID_CALL_NUDGE,
    LinsightModelResilienceMiddleware,
    _is_invalid_only_tool_call,
)


def _invalid_ai(name="ask_user"):
    return AIMessage(
        content="我先确认几个关键点，避免做错方向。",
        invalid_tool_calls=[{"name": name, "args": '{"questions": [', "id": "t1", "error": "bad json"}],
        response_metadata={"finish_reason": "tool_calls"},
    )


def _valid_ai():
    return AIMessage(
        content="",
        tool_calls=[{"name": "ask_user", "args": {"reason": "x"}, "id": "t2", "type": "tool_call"}],
        response_metadata={"finish_reason": "tool_calls"},
    )


def _ok_ai():
    return AIMessage(content="done", response_metadata={"finish_reason": "stop"})


def _resp(msg):
    return ModelResponse(result=[msg])


def _req():
    return ModelRequest(model=None, messages=[HumanMessage(content="和协作人员完成二手车调研")])


def _mw(limit=2):
    return LinsightModelResilienceMiddleware(max_retries=3, initial_delay=0.0, invalid_call_retry_limit=limit)


# ------------------------------------------------------------------ detection


def test_detect_invalid_only():
    assert _is_invalid_only_tool_call(_resp(_invalid_ai())) is True


def test_mixed_turn_is_not_invalid_only():
    ai = AIMessage(
        content="",
        tool_calls=[{"name": "write_file", "args": {"file_path": "x"}, "id": "t3", "type": "tool_call"}],
        invalid_tool_calls=[{"name": "ask_user", "args": "{", "id": "t4", "error": "bad json"}],
    )
    assert _is_invalid_only_tool_call(_resp(ai)) is False


def test_text_only_turn_is_not_invalid_only():
    assert _is_invalid_only_tool_call(_resp(_ok_ai())) is False


# ------------------------------------------------------------------ behaviour


async def test_retries_with_nudge_then_recovers():
    mw = _mw(limit=2)
    seen = []

    async def handler(request):
        seen.append(len(request.messages))
        return _resp(_invalid_ai()) if len(seen) == 1 else _resp(_valid_ai())

    out = await mw.awrap_model_call(_req(), handler)
    assert len(seen) == 2  # exactly one retry
    assert seen[1] == seen[0] + 1  # nudge appended to the retry request
    assert out.result[0].tool_calls  # the recovered call is returned


async def test_gives_up_after_limit_and_hands_off():
    mw = _mw(limit=2)
    calls = {"n": 0}

    async def handler(_request):
        calls["n"] += 1
        return _resp(_invalid_ai())

    out = await mw.awrap_model_call(_req(), handler)
    assert calls["n"] == 3  # initial + 2 bounded retries
    assert _is_invalid_only_tool_call(out) is True  # handed to the completion guard


async def test_nudge_names_the_tool_and_stays_ephemeral():
    mw = _mw(limit=1)
    captured = []

    async def handler(request):
        captured.append(list(request.messages))
        return _resp(_invalid_ai("write_file")) if len(captured) == 1 else _resp(_ok_ai())

    await mw.awrap_model_call(_req(), handler)
    last = captured[1][-1]
    assert isinstance(last, HumanMessage)
    assert last.content == _INVALID_CALL_NUDGE.format(tools="write_file")
    # Ephemeral: the first (un-nudged) request never carried the nudge.
    assert all(m.content != last.content for m in captured[0])


async def test_disabled_when_limit_zero():
    mw = _mw(limit=0)
    calls = {"n": 0}

    async def handler(_request):
        calls["n"] += 1
        return _resp(_invalid_ai())

    await mw.awrap_model_call(_req(), handler)
    assert calls["n"] == 1
