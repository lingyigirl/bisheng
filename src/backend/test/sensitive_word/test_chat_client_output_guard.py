"""ws output-guard contract tests (F056, AC-09..14).

Covers the three output guard surfaces:
- WorkflowClient ``_workflow_run`` event guarding (stream chunks + final frame rewrite)
- assistant stream callback token guarding (``AsyncStreamingLLMCallbackHandler``)
- the API-layer guard factory contract (flush / full_text / reset)
"""

from types import SimpleNamespace

import pytest

from bisheng.api.services.guardrail import build_output_guard
from bisheng.api.v1.callback import AsyncStreamingLLMCallbackHandler
from bisheng.api.v1.schemas import ChatResponse
from bisheng.common.chat.clients.workflow_client import WorkflowClient
from bisheng.common.chat.types import OutputGuardResult, WorkType
from bisheng.workflow.common.workflow import WorkflowStatus

POLICY_DAO_PATH = 'bisheng.sensitive_word.domain.models.sensitive_word_policy.SensitiveWordPolicyDao.get_policy'
HIT_LOG_PATH = 'bisheng.sensitive_word.domain.models.hit_log.SensitiveWordHitLogDao.acreate_async_best_effort'


@pytest.fixture(autouse=True)
def stub_platform_layer_off(monkeypatch):
    """Switch the F059 platform layer off: these tests cover the TENANT layer.

    The platform layer runs first on both directions since F059, so leaving it live
    would send every test to a real database for the platform policy table.
    """
    from bisheng.sensitive_word.domain.schemas import SensitiveWordCheckResult

    async def _no_platform_matcher():
        return None

    async def _no_platform_text(text):
        return SensitiveWordCheckResult(enabled=False, hits=[], auto_reply=None)

    monkeypatch.setattr(
        'bisheng.sensitive_word.domain.services.chat_guard.PlatformWordService.build_platform_matcher',
        _no_platform_matcher,
    )
    monkeypatch.setattr(
        'bisheng.sensitive_word.domain.services.chat_guard.PlatformWordService.check_platform_text',
        _no_platform_text,
    )


def _policy(*, enabled=True, words_types=None, custom_words='', auto_reply='命中敏感词', extra_config=None):
    return SimpleNamespace(
        enabled=enabled,
        words_types=words_types or ['custom'],
        custom_words=custom_words,
        auto_reply=auto_reply,
        extra_config=extra_config or {},
    )


class FakeWebSocket:
    def __init__(self):
        self.sent = []

    async def send_json(self, payload):
        self.sent.append(payload if isinstance(payload, dict) else payload.model_dump())


async def _noop_log(**kwargs):
    """Stand-in for the best-effort hit-log write; these tests are not about it."""
    return None


def _workflow_client(guard):
    client = WorkflowClient(
        request=SimpleNamespace(),
        client_key='ck-1',
        client_id='flow-1',
        chat_id='chat-1',
        user_id=7,
        login_user=SimpleNamespace(user_id=7, tenant_id=1),
        work_type=WorkType.WORKFLOW,
        websocket=FakeWebSocket(),
        output_guard=guard,
    )
    return client


def _stream_event(msg):
    # RedisCallback 产出的是 ChatResponse 模型(与真实链路一致)
    return ChatResponse(type='stream', category='stream_msg', message={'unique_id': 'u', 'node_id': 'n', 'msg': msg})


def _end_event(msg):
    return ChatResponse(type='end', category='stream_msg', message={'unique_id': 'u', 'node_id': 'n', 'msg': msg})


class _FakeWorkflow:
    def __init__(self, events, status=None):
        self.events = events
        self.status = status or {'status': WorkflowStatus.SUCCESS.value}

    async def get_response_until_break(self):
        for event in self.events:
            yield event

    async def async_get_workflow_status(self):
        return self.status

    async def async_clear_workflow_status(self):
        return None


class _OutputGuard:
    def __init__(self, results=None, full=''):
        self.results = results or {}
        self.full = full
        self.calls = []
        self.reset_called = 0

    async def __call__(self, chat_id, chunk):
        self.calls.append(chunk)
        return self.results.get(chunk, OutputGuardResult(safe_text=chunk))

    async def full_text(self):
        return self.full

    async def reset(self):
        self.reset_called += 1


async def test_workflow_stream_event_is_masked(monkeypatch):
    guard = _OutputGuard(results={'这是敏感词': OutputGuardResult(safe_text='这是***')})
    client = _workflow_client(guard)
    client.workflow = _FakeWorkflow([_stream_event('这是敏感词')])

    await client._workflow_run()

    sent = [f for f in client.websocket.sent if f.get('type') == 'stream']
    assert sent[0]['message']['msg'] == '这是***'


async def test_workflow_blocked_stream_truncates_and_drops_rest(monkeypatch):
    guard = _OutputGuard(results={'这是敏感词': OutputGuardResult(safe_text='这是话术', blocked=True)})
    client = _workflow_client(guard)
    client.workflow = _FakeWorkflow([_stream_event('这是敏感词'), _stream_event('后续内容')])

    await client._workflow_run()

    sent = [f for f in client.websocket.sent if f.get('type') == 'stream']
    assert len(sent) == 1  # 命中后剩余分片丢弃 (AC-10)
    assert sent[0]['message']['msg'] == '这是话术'


async def test_workflow_final_frame_rewritten_with_guarded_text(monkeypatch):
    guard = _OutputGuard(full='这是***的最终答案')
    client = _workflow_client(guard)
    client.workflow = _FakeWorkflow([_end_event('未过滤的最终答案')])

    await client._workflow_run()

    end_frames = [f for f in client.websocket.sent if f.get('type') == 'end']
    assert end_frames[0]['message']['msg'] == '这是***的最终答案'  # design 坑 6


async def test_workflow_events_pass_through_without_guard(monkeypatch):
    client = _workflow_client(guard=None)
    events = [_stream_event('原始内容'), _end_event('原始最终答案')]
    client.workflow = _FakeWorkflow(events)

    await client._workflow_run()

    sent = [f for f in client.websocket.sent if f.get('type') in ('stream', 'end')]
    assert [f['message']['msg'] for f in sent] == ['原始内容', '原始最终答案']


async def test_assistant_callback_masks_tokens_and_feeds_guarded_queue(monkeypatch):
    ws = FakeWebSocket()
    stream_queue = SimpleNamespace()
    queued = []
    stream_queue.put = queued.append
    handler = AsyncStreamingLLMCallbackHandler(
        websocket=ws, flow_id='assistant-1', chat_id='chat-1', user_id=7, stream_queue=stream_queue
    )
    handler._output_guard = _OutputGuard(results={'敏感词': OutputGuardResult(safe_text='***')})

    await handler.on_llm_new_token('敏感词', chunk=None)

    sent_token = ws.sent[-1]['message']['content']
    assert sent_token == '***'
    assert [item for item in queued if item.get('type') == 'answer'] == [{'type': 'answer', 'content': '***'}]


async def test_assistant_callback_blocks_and_stops_following_tokens(monkeypatch):
    ws = FakeWebSocket()
    handler = AsyncStreamingLLMCallbackHandler(websocket=ws, flow_id='a', chat_id='c', user_id=7, stream_queue=None)
    handler._output_guard = _OutputGuard(results={'敏感词': OutputGuardResult(safe_text='前缀话术', blocked=True)})

    await handler.on_llm_new_token('敏感词', chunk=None)
    await handler.on_llm_new_token('后续', chunk=None)

    contents = [f['message']['content'] for f in ws.sent]
    assert contents == ['前缀话术']  # 命中后后续 token 全部丢弃 (AC-10)


async def test_output_guard_factory_flush_and_full_text(monkeypatch):
    monkeypatch.setattr(POLICY_DAO_PATH, lambda **kwargs: _policy(custom_words='敏感词'))
    async def _no_log(**kwargs):
        pass

    monkeypatch.setattr(HIT_LOG_PATH, _no_log)
    guard = build_output_guard(SimpleNamespace(user_id=7, tenant_id=1), flow_id='flow-1')

    # 跨分片 mask: '这是敏' 只发出 '这','感词结束' 冲刷尾部并收尾
    first = await guard('chat-1', '这是敏')
    second = await guard('chat-1', '感词结束')
    flushed = await guard('chat-1', '')

    assert first.safe_text == '这'
    assert second.safe_text == '是***结束'
    assert flushed.safe_text == ''
    assert await guard.full_text() == '这是***结束'


async def test_output_guard_factory_blocked_full_text_includes_reply(monkeypatch):
    monkeypatch.setattr(POLICY_DAO_PATH, lambda **kwargs: _policy(custom_words='敏感词', extra_config={'action': 'block'}))
    async def _no_log(**kwargs):
        pass

    monkeypatch.setattr(HIT_LOG_PATH, _no_log)
    guard = build_output_guard(SimpleNamespace(user_id=7, tenant_id=1), flow_id='flow-1')

    result = await guard('chat-1', '这是敏感词的内容')

    assert result.blocked is True
    assert result.safe_text == '这是命中敏感词'  # 安全前缀 + 话术收尾
    assert await guard.full_text() == '这是命中敏感词'


async def test_output_guard_factory_reset_starts_fresh_round(monkeypatch):
    monkeypatch.setattr(POLICY_DAO_PATH, lambda **kwargs: _policy(custom_words='敏感词', extra_config={'action': 'block'}))
    async def _no_log(**kwargs):
        pass

    monkeypatch.setattr(HIT_LOG_PATH, _no_log)
    guard = build_output_guard(SimpleNamespace(user_id=7, tenant_id=1), flow_id='flow-1')

    await guard('chat-1', '这是敏感词')
    await guard.reset()
    result = await guard('chat-1', '正常内容')

    assert result.blocked is False
    assert result.safe_text == '正常内容'
    assert await guard.full_text() == '正常内容'


# --- H6: whole-message frames must be guarded too ----------------------------


def _whole_message_event(category, msg):
    """An `on_output_msg`-style frame: one COMPLETE answer string, type='over'.

    Mirrors what the worker emits (`redis_callback.py` on_output_msg /
    on_output_input / on_output_choose). Note it matches neither of the two
    conditions the guard used to require: the category is not `stream_msg` and
    the type is not stream/end/end_cover.
    """
    return ChatResponse(type='over', category=category, message={'unique_id': 'u', 'node_id': 'n', 'msg': msg})


@pytest.mark.parametrize(
    'category',
    ['output_msg', 'output_with_input_msg', 'output_with_choose_msg'],
)
async def test_whole_message_frames_are_masked(monkeypatch, category):
    """These frames carry user-visible text and used to bypass the guard entirely.

    Measured before the fix: a cached or non-streaming LLM node's output reached
    the client verbatim, and was persisted verbatim (`redis_callback` saves the
    same frame). The category check admitted only `stream_msg` and the type
    check only stream/end/end_cover, so BOTH had to be relaxed for text to get
    through unchecked -- which is why a fix to either one alone would not have
    helped.
    """
    monkeypatch.setattr(POLICY_DAO_PATH, lambda **kwargs: _policy(custom_words='敏感词', extra_config={'action': 'mask'}))
    monkeypatch.setattr(HIT_LOG_PATH, _noop_log)

    guard = build_output_guard(SimpleNamespace(user_id=7, tenant_id=1), flow_id='flow-1')
    client = _workflow_client(guard)

    result = await client._guard_output_event(_whole_message_event(category, '这是敏感词内容'))

    assert result is not None
    assert '敏感词' not in result.message['msg'], f'{category} bypassed the guard: {result.message["msg"]!r}'
    # '敏感词' is three characters, so the mask is three asterisks.
    assert result.message['msg'] == '这是***内容'


@pytest.mark.parametrize(
    'category',
    ['output_msg', 'output_with_input_msg', 'output_with_choose_msg'],
)
async def test_whole_message_frames_are_blocked_when_action_is_block(monkeypatch, category):
    """A block-mode hit on a whole-message frame must drop the offending text,
    not just mask it."""
    monkeypatch.setattr(POLICY_DAO_PATH, lambda **kwargs: _policy(custom_words='敏感词', extra_config={'action': 'block'}))
    monkeypatch.setattr(HIT_LOG_PATH, _noop_log)

    guard = build_output_guard(SimpleNamespace(user_id=7, tenant_id=1), flow_id='flow-1')
    client = _workflow_client(guard)

    result = await client._guard_output_event(_whole_message_event(category, '这是敏感词内容'))

    assert result is not None
    # Assert on the SURROUNDING text, not on the word itself: the close-out
    # phrase is '命中敏感词', which legitimately contains it. What must not
    # survive is the answer's own text after the cut.
    assert '内容' not in result.message['msg'], f'{category} leaked through block mode: {result.message["msg"]!r}'


async def test_control_frames_are_not_rewritten(monkeypatch):
    """The guard must stay off frames that carry no answer text. Running it over
    control frames would waste work and risk rewriting a `msg` that is not an
    answer."""
    monkeypatch.setattr(POLICY_DAO_PATH, lambda **kwargs: _policy(custom_words='敏感词', extra_config={'action': 'mask'}))
    monkeypatch.setattr(HIT_LOG_PATH, _noop_log)

    guard = build_output_guard(SimpleNamespace(user_id=7, tenant_id=1), flow_id='flow-1')
    client = _workflow_client(guard)

    event = ChatResponse(type='over', category='node_run', message={'unique_id': 'u', 'node_id': 'n', 'msg': '这是敏感词'})
    result = await client._guard_output_event(event)

    assert result.message['msg'] == '这是敏感词', 'a control frame was rewritten'
