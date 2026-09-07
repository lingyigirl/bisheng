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

    async def __call__(self, chat_id, text):
        self.calls.append(text)
        return self.reply


class _OutputGuard:
    def __init__(self, results=None):
        self.results = results or {}
        self.calls = []

    async def __call__(self, chat_id, chunk):
        self.calls.append(chunk)
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
