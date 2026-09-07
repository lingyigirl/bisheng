"""ws input-guard contract tests for both chat client classes (F056, AC-02/05/06).

The clients never import the sensitive-word domain directly (common → domain would
violate C1): the guard is an injected callable built by the API layer
(design 决策 2 + RULE-1). These tests pin that contract:

- ``input_guard(chat_id, text) -> auto_reply | None`` (async)
- blocked input → no dispatch, an answer-shaped frame with the auto_reply + close
- allowed input → normal dispatch, guard never touches control frames
"""

from types import SimpleNamespace

from bisheng.common.chat.client import ChatClient
from bisheng.common.chat.clients.workflow_client import WorkflowClient
from bisheng.common.chat.types import WorkType

WORKFLOW_THREADPOOL_PATH = 'bisheng.common.chat.clients.base.thread_pool'
ASSISTANT_THREADPOOL_PATH = 'bisheng.common.chat.client.thread_pool'


class FakeWebSocket:
    def __init__(self):
        self.sent = []

    async def send_json(self, payload):
        self.sent.append(payload if isinstance(payload, dict) else payload.model_dump())

    async def send_text(self, text):
        self.sent.append(text)

    def frames_of_type(self, msg_type):
        return [f for f in self.sent if isinstance(f, dict) and f.get('type') == msg_type]


class _InputGuard:
    def __init__(self, reply=None):
        self.reply = reply
        self.calls = []

    async def __call__(self, chat_id, text):
        self.calls.append((chat_id, text))
        return self.reply


def _workflow_client(guard):
    ws = FakeWebSocket()
    client = WorkflowClient(
        request=SimpleNamespace(),
        client_key='ck-1',
        client_id='flow-1',
        chat_id='chat-1',
        user_id=7,
        login_user=SimpleNamespace(user_id=7, tenant_id=1),
        work_type=WorkType.WORKFLOW,
        websocket=ws,
        input_guard=guard,
    )
    return client, ws


def _assistant_client(guard):
    # Bypass the constructor: it reads config/DB which unit tests must not touch.
    ws = FakeWebSocket()
    client = ChatClient.__new__(ChatClient)
    client.client_key = 'ck-2'
    client.client_id = 'assistant-1'
    client.chat_id = 'chat-2'
    client.user_id = 7
    client.work_type = WorkType.GPTS
    client.websocket = ws
    client.input_guard = guard
    client.task_ids = []
    return client, ws


async def test_workflow_blocked_input_is_not_dispatched(monkeypatch):
    submitted = []
    monkeypatch.setattr(WORKFLOW_THREADPOOL_PATH, SimpleNamespace(submit=lambda *a, **kw: submitted.append(a)))
    guard = _InputGuard(reply='命中敏感词,请修改输入')
    client, ws = _workflow_client(guard)

    await client.handle_message({'action': 'input', 'data': {'node-1': {'message': '包含敏感词的内容'}}})

    assert guard.calls == [('chat-1', '包含敏感词的内容')]
    assert submitted == []  # 不进入分发/执行 (AC-05)
    # type='over' 是客户端按完整 bot 消息渲染的答案帧 (与引擎错误帧同形状, 决策 6B)
    answer_frames = ws.frames_of_type('over')
    assert answer_frames and answer_frames[0]['message'] == '命中敏感词,请修改输入'
    assert ws.frames_of_type('close')  # 正常收尾帧


async def test_workflow_allowed_input_is_dispatched(monkeypatch):
    submitted = []
    monkeypatch.setattr(WORKFLOW_THREADPOOL_PATH, SimpleNamespace(submit=lambda *a, **kw: submitted.append(a)))
    guard = _InputGuard(reply=None)
    client, _ = _workflow_client(guard)

    await client.handle_message({'action': 'input', 'data': {'node-1': {'message': '正常内容'}}})

    assert guard.calls == [('chat-1', '正常内容')]
    assert len(submitted) == 1  # 正常分发执行 (AC-06)


async def test_workflow_control_frames_never_guarded(monkeypatch):
    submitted = []
    monkeypatch.setattr(WORKFLOW_THREADPOOL_PATH, SimpleNamespace(submit=lambda *a, **kw: submitted.append(a)))
    guard = _InputGuard(reply='不应触发')
    client, _ = _workflow_client(guard)

    for message in (
        {'action': 'init_data', 'data': {'flow_id': 'flow-1'}},
        {'action': 'check_status', 'chat_id': 'chat-1'},
    ):
        await client.handle_message(message)

    assert guard.calls == []
    assert len(submitted) == 2  # 控制帧直通 (design 坑 10)


async def test_workflow_stop_never_guarded(monkeypatch):
    handled = []
    guard = _InputGuard(reply='不应触发')
    client, _ = _workflow_client(guard)

    async def fake_handle(message):
        handled.append(message)

    client._handle_message = fake_handle  # type: ignore[method-assign]

    await client.handle_message({'action': 'stop'})

    assert guard.calls == []
    assert handled == [{'action': 'stop'}]


async def test_workflow_non_text_payload_skipped(monkeypatch):
    submitted = []
    monkeypatch.setattr(WORKFLOW_THREADPOOL_PATH, SimpleNamespace(submit=lambda *a, **kw: submitted.append(a)))
    guard = _InputGuard(reply='不应触发')
    client, _ = _workflow_client(guard)

    # message 字段缺失或非字符串(表单/结构化输入)→ 不检测,直通 (design 坑 10)
    await client.handle_message({'action': 'input', 'data': {'node-1': {'data': {'a': 1}}}})
    await client.handle_message({'action': 'input', 'data': {'node-1': {'message': {'json': True}}}})

    assert guard.calls == []
    assert len(submitted) == 2


async def test_workflow_first_blocked_node_short_circuits(monkeypatch):
    submitted = []
    monkeypatch.setattr(WORKFLOW_THREADPOOL_PATH, SimpleNamespace(submit=lambda *a, **kw: submitted.append(a)))
    guard = _InputGuard(reply='拦截')
    client, _ = _workflow_client(guard)

    await client.handle_message(
        {
            'action': 'input',
            'data': {
                'node-1': {'message': '第一个节点'},
                'node-2': {'message': '第二个节点'},
            },
        }
    )

    assert guard.calls == [('chat-1', '第一个节点')]  # 首个命中即拦截,不检测后续节点
    assert submitted == []


async def test_assistant_blocked_input_is_not_dispatched(monkeypatch):
    submitted = []
    monkeypatch.setattr(ASSISTANT_THREADPOOL_PATH, SimpleNamespace(submit=lambda *a, **kw: submitted.append(a)))
    guard = _InputGuard(reply='命中敏感词,请修改输入')
    client, ws = _assistant_client(guard)

    await client.handle_message({'inputs': {'input': '包含敏感词的问题'}, 'chat_id': 'chat-2', 'flow_id': 'assistant-1'})

    assert guard.calls == [('chat-2', '包含敏感词的问题')]
    assert submitted == []
    end_frames = ws.frames_of_type('end')
    assert end_frames and end_frames[0]['message'] == '命中敏感词,请修改输入'
    assert ws.frames_of_type('close')


async def test_assistant_allowed_input_is_dispatched(monkeypatch):
    submitted = []
    monkeypatch.setattr(ASSISTANT_THREADPOOL_PATH, SimpleNamespace(submit=lambda *a, **kw: submitted.append(a)))
    guard = _InputGuard(reply=None)
    client, _ = _assistant_client(guard)

    await client.handle_message({'inputs': {'input': '正常问题'}})

    assert guard.calls == [('chat-2', '正常问题')]
    assert len(submitted) == 1


async def test_assistant_session_switch_never_guarded(monkeypatch):
    submitted = []
    monkeypatch.setattr(ASSISTANT_THREADPOOL_PATH, SimpleNamespace(submit=lambda *a, **kw: submitted.append(a)))
    guard = _InputGuard(reply='不应触发')
    client, _ = _assistant_client(guard)

    # 会话切换消息无 input 字段 → 不检测,直通 (design 坑 10)
    await client.handle_message({'inputs': {'data': {'id': 'assistant-2', 'chatId': 'chat-9'}}})

    assert guard.calls == []
    assert len(submitted) == 1


async def test_guard_disabled_when_not_injected(monkeypatch):
    submitted = []
    monkeypatch.setattr(WORKFLOW_THREADPOOL_PATH, SimpleNamespace(submit=lambda *a, **kw: submitted.append(a)))
    client, _ = _workflow_client(guard=None)

    await client.handle_message({'action': 'input', 'data': {'node-1': {'message': '任意内容'}}})

    assert len(submitted) == 1  # 未注入 guard = 零行为变化 (AC-02/AC-17)
