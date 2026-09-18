"""HTTP OpenAI-compatible endpoint guard wiring tests (F056, AC-06/08/14).

按 tasks.md T011 降级口径:完整链路需真实模型/工作流执行, 本文件只锁定
「检测门面在端点内的调用接线」——直接驱动端点函数 + monkeypatch 依赖,
断言 400+11201 返回形态 / 输出重写 / 流式分片守卫.
"""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

from langchain_core.messages import AIMessage, AIMessageChunk

from bisheng.api.v1.schemas import OpenAIChatCompletionReq
from bisheng.common.chat.types import OutputGuardResult
from bisheng.open_endpoints.api.endpoints.assistant import assistant_chat_completions

ENDPOINT_PATH = 'bisheng.open_endpoints.api.endpoints.assistant'


def _req(messages, stream=False):
    return OpenAIChatCompletionReq(
        model='11111111111111111111111111111111', messages=messages, stream=stream, temperature=0
    )


class _InputGuard:
    def __init__(self, reply=None):
        self.reply = reply
        self.calls = []
        # Recorded separately so tests can assert the endpoint passes a real
        # chat_id -- it used to pass None, which filed every HTTP hit as
        # `chat_id = NULL` and left a request's hits unlinkable (L2).
        self.chat_ids = []

    async def __call__(self, chat_id, text):
        self.calls.append(text)
        self.chat_ids.append(chat_id)
        return self.reply


class _OutputGuard:
    def __init__(self, results=None):
        self.results = results or {}
        self.calls = []
        self.chat_ids = []

    async def __call__(self, chat_id, chunk):
        self.calls.append(chunk)
        self.chat_ids.append(chat_id)
        return self.results.get(chunk, OutputGuardResult(safe_text=chunk))

    async def full_text(self):
        return ''.join(self.results.get(c, OutputGuardResult(safe_text=c)).safe_text for c in self.calls)


class _FakeAgent:
    def __init__(self, answer='答案', chunks=None):
        self.answer = answer
        self.chunks = chunks or []
        self.llm = SimpleNamespace()

    async def init_assistant(self):
        return None

    async def run(self, question, chat_history):
        return [AIMessage(content=self.answer)]

    async def astream(self, question, chat_history):
        for chunk in self.chunks:
            yield [AIMessageChunk(content=chunk)]


def _setup(monkeypatch, input_guard=None, output_guard=None, *, tenant_id=1):
    monkeypatch.setattr(f'{ENDPOINT_PATH}.get_default_operator', lambda: SimpleNamespace(user_id=7, tenant_id=tenant_id))
    monkeypatch.setattr(f'{ENDPOINT_PATH}.get_current_tenant_id', lambda: tenant_id)
    monkeypatch.setattr(f'{ENDPOINT_PATH}.get_request_ip', lambda request: 'test-ip')
    monkeypatch.setattr(
        f'{ENDPOINT_PATH}.telemetry_service', SimpleNamespace(log_event=AsyncMock())
    )
    monkeypatch.setattr(
        f'{ENDPOINT_PATH}.AssistantService.get_assistant_info',
        AsyncMock(return_value=SimpleNamespace(temperature=0, name='assistant-a')),
    )
    monkeypatch.setattr(f'{ENDPOINT_PATH}.build_input_guard', lambda login_user, flow_id: input_guard)
    monkeypatch.setattr(f'{ENDPOINT_PATH}.build_output_guard', lambda login_user, flow_id: output_guard)


async def test_input_blocked_returns_400_with_11201_and_reply(monkeypatch):
    input_guard = _InputGuard(reply='命中敏感词,请修改输入')
    _setup(monkeypatch, input_guard=input_guard)
    agent = _FakeAgent()
    monkeypatch.setattr(f'{ENDPOINT_PATH}.AssistantAgent', lambda *a, **kw: agent)

    response = await assistant_chat_completions(SimpleNamespace(), _req([{'role': 'user', 'content': '包含敏感词'}]))

    assert response.status_code == 400
    body = json.loads(response.body)
    assert body['status_code'] == 11201
    assert body['status_message'] == '命中敏感词,请修改输入'
    assert input_guard.calls == ['包含敏感词']  # 仅检测最新用户消息 (AC-08)


async def test_input_allowed_and_output_masked_non_streaming(monkeypatch):
    input_guard = _InputGuard(reply=None)
    output_guard = _OutputGuard(results={'这是敏感词的答案': OutputGuardResult(safe_text='这是***的答案')})
    _setup(monkeypatch, input_guard=input_guard, output_guard=output_guard)
    monkeypatch.setattr(f'{ENDPOINT_PATH}.AssistantAgent', lambda *a, **kw: _FakeAgent(answer='这是敏感词的答案'))

    response = await assistant_chat_completions(SimpleNamespace(), _req([{'role': 'user', 'content': '正常问题'}]))

    assert response.choices[0].message['content'] == '这是***的答案'
    assert input_guard.calls == ['正常问题']


async def test_output_blocked_non_streaming_answer_is_closeout(monkeypatch):
    _setup(monkeypatch, input_guard=_InputGuard(reply=None), output_guard=_OutputGuard())
    blocked_guard = _OutputGuard(results={'违规答案': OutputGuardResult(safe_text='前缀命中敏感词', blocked=True)})
    monkeypatch.setattr(f'{ENDPOINT_PATH}.build_output_guard', lambda login_user, flow_id: blocked_guard)
    monkeypatch.setattr(f'{ENDPOINT_PATH}.AssistantAgent', lambda *a, **kw: _FakeAgent(answer='违规答案'))

    response = await assistant_chat_completions(SimpleNamespace(), _req([{'role': 'user', 'content': '正常问题'}]))

    assert response.choices[0].message['content'] == '前缀命中敏感词'


async def test_streaming_output_guarded_per_chunk(monkeypatch):
    _setup(monkeypatch, input_guard=_InputGuard(reply=None))
    output_guard = _OutputGuard(results={'敏感': OutputGuardResult(safe_text=''), '词的内容': OutputGuardResult(safe_text='***的内容')})
    monkeypatch.setattr(f'{ENDPOINT_PATH}.build_output_guard', lambda login_user, flow_id: output_guard)
    monkeypatch.setattr(f'{ENDPOINT_PATH}._check_model_supports_streaming', lambda agent: True)
    monkeypatch.setattr(
        f'{ENDPOINT_PATH}.AssistantAgent', lambda *a, **kw: _FakeAgent(chunks=['敏感', '词的内容'])
    )

    response = await assistant_chat_completions(
        SimpleNamespace(), _req([{'role': 'user', 'content': '正常问题'}], stream=True)
    )

    deltas = []
    async for line in response.body_iterator:
        if line.startswith('data: ') and '[DONE]' not in line:
            payload = json.loads(line.removeprefix('data: '))
            delta = payload['choices'][0]['delta']
            if 'content' in delta:
                deltas.append(delta['content'])
    assert ''.join(deltas) == '***的内容'  # 逐分片守卫 (AC-14 接线口径)


# --- L2: the HTTP path must carry a real chat_id -----------------------------


async def test_http_path_passes_a_non_null_chat_id(monkeypatch):
    """The OpenAI-compatible endpoint is stateless but still needs an identifier.

    `chat_id` is what ties a request's INPUT check to its OUTPUT check in the
    hit log. Passing None -- which this endpoint did -- filed every HTTP hit as
    `chat_id = NULL`, so an admin reading the log could not tell which output
    hit belonged to which input hit.
    """
    input_guard = _InputGuard(reply=None)
    output_guard = _OutputGuard(results={'正常答案': OutputGuardResult(safe_text='正常答案')})
    _setup(monkeypatch, input_guard=input_guard, output_guard=output_guard)
    monkeypatch.setattr(f'{ENDPOINT_PATH}.AssistantAgent', lambda *a, **kw: _FakeAgent(answer='正常答案'))

    await assistant_chat_completions(SimpleNamespace(), _req([{'role': 'user', 'content': '问题'}]))

    assert input_guard.chat_ids, 'the input guard was never called'
    assert all(cid for cid in input_guard.chat_ids), (
        f'the endpoint passed a falsy chat_id: {input_guard.chat_ids}'
    )
    assert output_guard.chat_ids, 'the output guard was never called'
    assert all(cid for cid in output_guard.chat_ids), (
        f'the output guard got a falsy chat_id: {output_guard.chat_ids}'
    )


async def test_input_and_output_of_one_request_share_a_chat_id(monkeypatch):
    """The point of generating one per request: its input hit and its output hit
    must be linkable to each other."""
    input_guard = _InputGuard(reply=None)
    output_guard = _OutputGuard(results={'正常答案': OutputGuardResult(safe_text='正常答案')})
    _setup(monkeypatch, input_guard=input_guard, output_guard=output_guard)
    monkeypatch.setattr(f'{ENDPOINT_PATH}.AssistantAgent', lambda *a, **kw: _FakeAgent(answer='正常答案'))

    await assistant_chat_completions(SimpleNamespace(), _req([{'role': 'user', 'content': '问题'}]))

    assert len(set(input_guard.chat_ids)) == 1, f'input used several ids: {input_guard.chat_ids}'
    assert len(set(output_guard.chat_ids)) == 1, f'output used several ids: {output_guard.chat_ids}'
    assert input_guard.chat_ids[0] == output_guard.chat_ids[0], (
        'input and output of the same request were logged under different ids'
    )


async def test_two_requests_do_not_share_a_chat_id(monkeypatch):
    """Generated per request, so two calls must not collide -- otherwise the log
    would merge two unrelated requests into one conversation."""
    seen = []

    for _ in range(2):
        input_guard = _InputGuard(reply=None)
        _setup(monkeypatch, input_guard=input_guard, output_guard=_OutputGuard())
        monkeypatch.setattr(f'{ENDPOINT_PATH}.AssistantAgent', lambda *a, **kw: _FakeAgent(answer='正常答案'))

        await assistant_chat_completions(SimpleNamespace(), _req([{'role': 'user', 'content': '问题'}]))
        seen.append(input_guard.chat_ids[0])

    assert seen[0] != seen[1], f'both requests used {seen[0]!r}'
