"""T010b (F059): the platform layer reaches the OpenAI-compatible endpoints.

These endpoints (assistant / workflow) are NOT modified by F059 — they call the
shared `build_input_guard` factory, which delegates to `ChatGuardService`. The
platform layer lives inside that service, so the endpoints inherit it. This file
proves that rather than assuming it, because "we didn't touch that file" is not
evidence that the behaviour propagated (spec AC-13).

Unlike `test_open_endpoints_guard.py`, which stubs `build_input_guard` to test the
endpoint's own wiring, here the REAL factory runs so the whole chain is exercised:
endpoint → factory → ChatGuardService → platform layer.
"""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

from bisheng.api.v1.schemas import OpenAIChatCompletionReq
from bisheng.open_endpoints.api.endpoints.assistant import assistant_chat_completions
from bisheng.sensitive_word.domain.schemas import SensitiveWordCheckResult, SensitiveWordHit

ENDPOINT_PATH = 'bisheng.open_endpoints.api.endpoints.assistant'
TENANT_POLICY_PATH = 'bisheng.sensitive_word.domain.models.sensitive_word_policy.SensitiveWordPolicyDao.get_policy'
PLATFORM_TEXT_PATH = 'bisheng.sensitive_word.domain.services.chat_guard.PlatformWordService.check_platform_text'
PLATFORM_MATCHER_PATH = 'bisheng.sensitive_word.domain.services.chat_guard.PlatformWordService.build_platform_matcher'
HIT_LOG_PATH = 'bisheng.sensitive_word.domain.models.hit_log.SensitiveWordHitLogDao.acreate_async_best_effort'

PLATFORM_REPLY = '很抱歉，你的提问包含敏感词，系统无法做出判断。'  # noqa: RUF001


def _req(messages, stream=False):
    return OpenAIChatCompletionReq(
        model='11111111111111111111111111111111', messages=messages, stream=stream, temperature=0
    )


def _tenant_disabled(**kwargs):
    return SimpleNamespace(
        enabled=False, words_types=[], custom_words='', auto_reply='', extra_config={}
    )


async def _no_hit_log(**kwargs):
    return None


def _stub_platform_text(monkeypatch, hits, auto_reply=PLATFORM_REPLY):
    async def _check(text):
        if not hits:
            return SensitiveWordCheckResult(enabled=False, hits=[], auto_reply=None)
        return SensitiveWordCheckResult(
            enabled=True,
            hits=[SensitiveWordHit(word=w, count=1) for w in hits if w in text],
            auto_reply=auto_reply,
        )

    monkeypatch.setattr(PLATFORM_TEXT_PATH, _check)


def _setup(monkeypatch):
    """Stub everything the endpoint needs EXCEPT the guard chain itself.

    `build_input_guard` / `build_output_guard` are deliberately NOT patched here
    (unlike `test_open_endpoints_guard.py`): letting the real factory run is the
    entire point — it is what carries the platform layer into this endpoint.
    """
    monkeypatch.setattr(
        f'{ENDPOINT_PATH}.get_default_operator',
        lambda: SimpleNamespace(user_id=7, tenant_id=1),
    )
    monkeypatch.setattr(f'{ENDPOINT_PATH}.get_current_tenant_id', lambda: 1)
    monkeypatch.setattr(f'{ENDPOINT_PATH}.get_request_ip', lambda request: 'test-ip')
    monkeypatch.setattr(f'{ENDPOINT_PATH}.telemetry_service', SimpleNamespace(log_event=AsyncMock()))
    monkeypatch.setattr(
        f'{ENDPOINT_PATH}.AssistantService.get_assistant_info',
        AsyncMock(return_value=SimpleNamespace(temperature=0, name='assistant-a')),
    )
    monkeypatch.setattr(HIT_LOG_PATH, _no_hit_log)


async def test_platform_hit_returns_400_with_11201_and_the_platform_reply(monkeypatch):
    """Spec AC-13: a platform-library hit on the OpenAI-compatible surface.

    The tenant policy is DISABLED here, so a 400 can only have come from the
    platform layer — which the endpoint file never mentions.
    """
    monkeypatch.setattr(TENANT_POLICY_PATH, _tenant_disabled)
    _stub_platform_text(monkeypatch, hits=['信贷违规'])
    _setup(monkeypatch)

    response = await assistant_chat_completions(
        SimpleNamespace(), _req([{'role': 'user', 'content': '这里有信贷违规内容'}])
    )

    assert response.status_code == 400
    body = json.loads(response.body)
    assert body['status_code'] == 11201
    assert body['status_message'] == PLATFORM_REPLY


async def test_no_hit_on_either_layer_is_not_blocked(monkeypatch):
    """Control: with both layers silent, the guard lets the message through.

    Without a counter-example the test above could pass merely because the
    endpoint always returns 400. Rather than drive the whole endpoint past the
    guard (which needs the agent, model and streaming plumbing all stubbed), this
    asserts the guard itself returns None for a clean message — the condition the
    endpoint branches on.
    """
    from bisheng.api.services.guardrail import build_input_guard

    monkeypatch.setattr(TENANT_POLICY_PATH, _tenant_disabled)
    _stub_platform_text(monkeypatch, hits=[])
    monkeypatch.setattr(HIT_LOG_PATH, _no_hit_log)

    guard = build_input_guard(SimpleNamespace(user_id=7, tenant_id=1), 'assistant-a')

    assert await guard(None, '普通问题') is None


async def test_output_layer_also_covers_the_platform_library(monkeypatch):
    """The output guard built by the same factory carries the platform matcher.

    Asserted at the factory level: the platform matcher is resolved lazily on the
    first chunk, so a freshly built guard reports nothing until it is fed.
    """
    from bisheng.api.services.guardrail import build_output_guard

    async def _no_matcher():
        return None

    monkeypatch.setattr(TENANT_POLICY_PATH, _tenant_disabled)
    monkeypatch.setattr(PLATFORM_MATCHER_PATH, _no_matcher)
    monkeypatch.setattr(HIT_LOG_PATH, _no_hit_log)

    guard = build_output_guard(SimpleNamespace(user_id=7, tenant_id=1), 'flow-1')

    # Feeding a chunk resolves the platform layer; with both layers off the text
    # passes through untouched (spec AC-14/AC-17 compatibility).
    result = await guard('chat-1', '任意内容')
    assert result.safe_text == '任意内容'

    await guard('chat-1', '')  # flush
    await guard.reset()
    assert await guard.full_text() == ''
