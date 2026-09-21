"""F056 H8 step 2: the workstation chat stream must run the output policy.

`POST /api/v1/workstation/chat/completions` is a fourth chat entry point and
builds its own SSE stream rather than going through `ChatManager`. It had no
output guarding at all: the only thing protecting it was the persist-time
filter (`answer_filter`), which covers what is STORED but not what is
STREAMED — so a user watched the model's raw output and only the history copy
was cleaned up.

These tests assert the wiring rather than driving the whole generator: the
generator needs a model, tools and a conversation, and the property being
protected is that each token goes through the guard before it reaches a frame,
the event log or `final_msg`.
"""

from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from bisheng.api.v1.schema.chat_schema import APIChatCompletion
from bisheng.common.constants.enums.telemetry import ApplicationTypeEnum
from bisheng.workstation.domain.services import chat_service

SERVICE_PATH = Path(chat_service.__file__)


@pytest.fixture(autouse=True)
def no_platform_layer(monkeypatch):
    """Switch the F059 platform layer off; these tests are about the tenant layer.

    Without it `check_output_chunk` consults the platform policy table, which is
    a real MySQL round trip through a connection bound to the pytest event loop
    -- surfacing as `AttributeError: 'NoneType' object has no attribute 'send'`
    from aiomysql, which looks like a guarding bug and is not one. Same fixture
    and same reasoning as `test_chat_guard.py`.
    """
    from bisheng.sensitive_word.domain.schemas import SensitiveWordCheckResult

    async def _no_platform_matcher():
        return None

    async def _no_platform_text(text):
        return SensitiveWordCheckResult(enabled=False, hits=[], auto_reply=None)

    monkeypatch.setattr(
        'bisheng.sensitive_word.domain.services.platform_word_service.PlatformWordService.build_platform_matcher',
        _no_platform_matcher,
        raising=False,
    )
    monkeypatch.setattr(
        'bisheng.sensitive_word.domain.services.platform_word_service.PlatformWordService.check_platform_text',
        _no_platform_text,
        raising=False,
    )


def _data(**overrides) -> APIChatCompletion:
    fields = {
        'clientTimestamp': '2026-09-20T00:00:00Z',
        'conversationId': 'chat-1',
        'model': 'm1',
        'text': '你好',
    }
    fields.update(overrides)
    return APIChatCompletion(**fields)


# --- the wiring ---------------------------------------------------------------


def _agent_stream_source() -> str:
    return SERVICE_PATH.read_text(encoding='utf-8')


def test_the_agent_stream_builds_an_output_guard():
    """No guard means the stream is unfiltered; this is the line that changes.

    The second argument is asserted separately, below: it started as a
    conversation id, which is the bug that second test describes.
    """
    source = _agent_stream_source()
    assert 'build_output_guard(login_user, _WORKSTATION_FLOW_ID)' in source, (
        'the agent stream no longer builds an output guard -- the workstation '
        'entry would stream the model output unfiltered (H8)'
    )


def test_every_agent_answer_frame_goes_through_the_guard():
    """Both emission sites (the tool branch and the plain branch) must guard.

    Asserted by counting call sites rather than by behaviour, because the two
    branches are reached under different conditions and a behavioural test
    would only cover whichever one it happens to drive.
    """
    tree = ast.parse(_agent_stream_source())

    guarded_emissions = 0
    unguarded_emissions = 0
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not (isinstance(func, ast.Name) and func.id == '_sse_resp'):
            continue
        if len(node.args) < 2:
            continue
        category = node.args[0]
        if not (isinstance(category, ast.Constant) and category.value == 'agent_answer'):
            continue
        # Only the token-by-token frames. The other two `agent_answer` frames
        # carry `db_content` (the persisted row, already filtered) and `reply`
        # (the blocked-input close-out), neither of which is raw model output.
        kind = node.args[1]
        if not (isinstance(kind, ast.Constant) and kind.value == 'stream'):
            continue
        # The payload is the third positional arg: {"msg": <name>}.
        payload = node.args[2]
        msg_name = None
        if isinstance(payload, ast.Dict):
            for key, value in zip(payload.keys, payload.values, strict=True):
                if isinstance(key, ast.Constant) and key.value == 'msg':
                    msg_name = getattr(value, 'id', None)
        if msg_name == 'safe_text':
            guarded_emissions += 1
        else:
            unguarded_emissions += 1

    assert guarded_emissions == 2, (
        f'expected both streaming emissions to send guarded text, found {guarded_emissions}'
    )
    assert unguarded_emissions == 0, (
        f'{unguarded_emissions} agent_answer frame(s) still emit raw text'
    )


def test_final_msg_and_events_take_the_guarded_text():
    """Guarding only the frame would leave the persisted copy raw, and the
    event log is rendered from the same source."""
    source = _agent_stream_source()
    assert 'final_msg += safe_text' in source, 'final_msg still accumulates raw text'
    assert 'final_msg += text' not in source, 'a raw accumulator survived'
    assert '"content": safe_text' in source, 'the event log still records raw text'


# --- what these tests can and cannot catch ------------------------------------
#
# MEASURED LIMIT, recorded because it is easy to mistake coverage for safety:
# replacing `_guard_output_text`'s body with `return text` leaves every test in
# this file GREEN. The helper is a closure defined inside
# `_agent_stream_chat_completion`, so no test can call it directly, and any
# test that re-implements its logic is testing its own copy rather than the
# code -- I wrote exactly that mistake once before noticing.
#
# Catching it needs the generator driven end to end, which needs a model, a
# tool set and a conversation. `test/e2e/` is the right home for that and is
# where this belongs; until then the gap is stated rather than papered over.
#
# What IS covered here: the guard is built and reached at both emission sites
# (structural), the input side blocks and dispatches correctly (behavioural),
# and the guard's own masking is covered exhaustively in
# `test/sensitive_word/` where it can be driven directly.


# --- input side (H8 step 1) still holds ---------------------------------------


async def test_blocked_input_never_reaches_the_stream():
    """Regression guard for step 1: the input check must return a stream
    carrying the close-out phrase and never call the agent."""
    called = []

    async def fake_guard(chat_id, text):
        called.append(text)
        return '命中敏感词'

    with patch(
        'bisheng.api.services.guardrail.build_input_guard', lambda *a, **k: fake_guard
    ):
        response = await chat_service.stream_chat_completion(
            SimpleNamespace(), _data(text='含敏感词的问题'), SimpleNamespace(user_id=1, tenant_id=1)
        )

    body = ''.join([chunk async for chunk in response.body_iterator])
    assert '命中敏感词' in body, f'the close-out phrase is missing: {body[:200]!r}'
    assert called == ['含敏感词的问题']


async def test_allowed_input_dispatches_to_the_agent_branch():
    """The input check must not block an ordinary turn."""

    async def fake_guard(chat_id, text):
        return None

    dispatched = []
    with patch(
        'bisheng.api.services.guardrail.build_input_guard', lambda *a, **k: fake_guard
    ), patch.object(
        chat_service,
        '_agent_stream_chat_completion',
        AsyncMock(side_effect=lambda *a, **k: dispatched.append(True) or 'AGENT'),
    ):
        result = await chat_service.stream_chat_completion(
            SimpleNamespace(), _data(), SimpleNamespace(user_id=1, tenant_id=1)
        )

    assert result == 'AGENT'
    assert dispatched == [True]


# --- the hit log's `flow_id` on this entry point ------------------------------
#
# The daily chat is not bound to an application: `APIChatCompletion` carries no
# app id and `MessageSession.flow_id` is `''` for workstation sessions, so this
# entry point records the ENTRY-TYPE label instead. The ws entries pass a real
# `assistant_id` / `workflow_id` into the same column.
#
# MEASURED, before this was fixed: the output guard was built with
# `build_output_guard(login_user, conversation_id)` while the parameter is
# `flow_id`, so every workstation OUTPUT row stored a chat id where the ws rows
# store an app id -- 3 rows in the dev database had `flow_id == chat_id`, and
# all 3 were output rows. The input path passed `flow_id=None` and wrote NULL.
# Same column, three meanings, decided by direction.
#
# Both tests drive the real `check_chat_input`, so the `flow_id` under assertion
# is the one production builds -- stubbing that method would replace the very
# call that writes the log.

_DAILY_CHAT_LABEL = 'daily_chat'


def _workstation_flow_id() -> str:
    return chat_service._WORKSTATION_FLOW_ID


def test_no_workstation_guard_is_built_with_a_chat_id():
    """The old bug, asserted directly."""
    source = _agent_stream_source()
    assert 'build_output_guard(login_user, conversation_id)' not in source, (
        'the output guard is again built with a conversation id where the '
        'parameter is the hit log origin -- output rows would record a chat id '
        'as the app'
    )
    assert 'build_output_guard(login_user, _WORKSTATION_FLOW_ID)' in source, (
        'the output guard no longer records the daily-chat entry label'
    )


def _recording_hit_log(monkeypatch, word: str = 'w') -> list[dict]:
    """Capture hit-log writes, and force a platform-layer hit for `word`.

    The platform layer is the one that SHORT-CIRCUITS input checking, and it is
    the layer the reported bug was seen with. Patching it (rather than the
    tenant matcher) also keeps the assertion on the real `check_chat_input`.
    """
    from bisheng.sensitive_word.domain.models.hit_log import SensitiveWordHitLogDao
    from bisheng.sensitive_word.domain.schemas import (
        SensitiveWordCheckResult,
        SensitiveWordHit,
    )

    recorded: list[dict] = []

    async def _record(**kwargs):
        recorded.append(kwargs)

    async def _platform_text(text):
        return SensitiveWordCheckResult(
            enabled=True, hits=[SensitiveWordHit(word=word, count=1)], auto_reply='命中'
        )

    monkeypatch.setattr(SensitiveWordHitLogDao, 'acreate_async_best_effort', _record)
    monkeypatch.setattr(
        'bisheng.sensitive_word.domain.services.platform_word_service.PlatformWordService.check_platform_text',
        _platform_text,
        raising=False,
    )
    return recorded


async def test_blocked_input_records_the_daily_chat_entry_label(monkeypatch):
    """Before: this wrote NULL, so the log page showed '-' for every hit."""
    recorded = _recording_hit_log(monkeypatch)

    response = await chat_service.stream_chat_completion(
        SimpleNamespace(), _data(text='含敏感词的问题'), SimpleNamespace(user_id=1, tenant_id=1)
    )
    await response.body_iterator.__anext__()

    assert recorded, 'a blocked input must be logged'
    assert recorded[0]['direction'] == 'input'
    assert recorded[0]['flow_id'] == _workstation_flow_id(), (
        f"expected the entry-type label, got {recorded[0]['flow_id']!r}"
    )


def test_the_label_matches_the_enum_the_rest_of_the_service_uses():
    """A hand-written string would drift from the telemetry label; this pins
    them together so the log and the metrics cannot disagree."""
    assert _workstation_flow_id() == ApplicationTypeEnum.DAILY_CHAT.value
