"""服务账号 Key 调用助手的档位收口(2026-10-22)。

助手详情/列表仍按 `view_app|use_app` 读取;对话(HTTP 与 WS)与工作流一致要求
`use_app`(editor 档起)。非密钥调用不受影响。
"""

from types import SimpleNamespace

import pytest

from bisheng.common.errcode.http_error import UnAuthorizedError
from bisheng.common.errcode.open_api import OpenApiNotFoundError
from bisheng.open_api.domain.context import (
    OpenApiPrincipal,
    reset_open_api_principal,
    set_open_api_principal,
)
from bisheng.open_endpoints.api.endpoints import assistant as assistant_ep

ASSISTANT_ID = "12345678123456781234567812345678"


def _principal() -> OpenApiPrincipal:
    return OpenApiPrincipal(
        credential_id=1,
        tenant_id=1,
        service_account_id=2,
        owner_user_id=3,
        key_mask="bs-sak-********AbCd",
        scopes=("assistant:invoke",),
        owner_payload=SimpleNamespace(user_id=3),
        allowed_apps=None,
    )


def _request():
    return SimpleNamespace(headers={}, client=SimpleNamespace(host="127.0.0.1"))


def _chat_req():
    from bisheng.api.v1.schemas import OpenAIChatCompletionReq

    return OpenAIChatCompletionReq(
        model=ASSISTANT_ID,
        messages=[{"role": "user", "content": "hi"}],
        stream=False,
    )


@pytest.fixture
def key_principal():
    token = set_open_api_principal(_principal())
    try:
        yield
    finally:
        reset_open_api_principal(token)


async def test_http_invoke_requires_use_app_for_viewer_key(monkeypatch, key_principal):
    """viewer-only Key 调助手对话 → 404/11809,且不会进入取助手信息的逻辑。"""
    from bisheng.api.services.assistant import AssistantService
    from bisheng.permission.domain.services.application_permission_service import ApplicationPermissionService

    calls = []

    async def fake_has_any(user, object_type, object_id, permission_ids):
        calls.append((object_type, tuple(permission_ids)))
        return False

    monkeypatch.setattr(ApplicationPermissionService, "has_any_permission_async", fake_has_any)
    monkeypatch.setattr(assistant_ep, "get_default_operator", lambda: SimpleNamespace(user_id=3))

    async def must_not_call(*args, **kwargs):
        raise AssertionError("get_assistant_info must not run after the invoke gate denies")

    monkeypatch.setattr(AssistantService, "get_assistant_info", must_not_call)

    with pytest.raises(OpenApiNotFoundError):
        await assistant_ep.assistant_chat_completions(_request(), _chat_req())

    assert calls == [("assistant", ("use_app",))]


async def test_http_invoke_allows_editor_key(monkeypatch, key_principal):
    """editor 档(含 use_app)放行,后续进入取助手信息逻辑。"""
    from bisheng.api.services.assistant import AssistantService
    from bisheng.permission.domain.services.application_permission_service import ApplicationPermissionService

    calls = []

    async def fake_has_any(user, object_type, object_id, permission_ids):
        calls.append((object_type, tuple(permission_ids)))
        return True

    monkeypatch.setattr(ApplicationPermissionService, "has_any_permission_async", fake_has_any)
    monkeypatch.setattr(assistant_ep, "get_default_operator", lambda: SimpleNamespace(user_id=3))

    async def stop_after_gate(*args, **kwargs):
        raise UnAuthorizedError()

    monkeypatch.setattr(AssistantService, "get_assistant_info", stop_after_gate)

    with pytest.raises(UnAuthorizedError):
        await assistant_ep.assistant_chat_completions(_request(), _chat_req())

    assert calls == [("assistant", ("use_app",))]


async def test_http_invoke_skips_tier_check_without_principal(monkeypatch):
    """非密钥调用(平台用户/访客)行为不变:不做资源档位判定。"""
    from bisheng.api.services.assistant import AssistantService
    from bisheng.permission.domain.services.application_permission_service import ApplicationPermissionService

    async def must_not_call(*args, **kwargs):
        raise AssertionError("permission check must not run for non-key calls")

    monkeypatch.setattr(ApplicationPermissionService, "has_any_permission_async", must_not_call)
    monkeypatch.setattr(assistant_ep, "get_default_operator", lambda: SimpleNamespace(user_id=3))

    async def boom(*args, **kwargs):
        raise RuntimeError("reached assistant info")

    monkeypatch.setattr(AssistantService, "get_assistant_info", boom)

    response = await assistant_ep.assistant_chat_completions(_request(), _chat_req())
    # 旧行为:取助手信息失败走 500 兜底,而不是 11809。
    assert response.status_code == 500


class _FakeWebSocket:
    def __init__(self):
        self.headers = {}
        self.client = SimpleNamespace(host="127.0.0.1")
        self.closed = None

    async def close(self, code=None, reason=None):
        self.closed = (code, reason)


async def test_ws_closes_1008_when_use_app_missing(monkeypatch, key_principal):
    """WS 密钥调用建连即校验 use_app;不足则 1008/11809,且不进入会话分发。"""
    from bisheng.permission.domain.services.application_permission_service import ApplicationPermissionService

    async def fake_has_any(user, object_type, object_id, permission_ids):
        return False

    monkeypatch.setattr(ApplicationPermissionService, "has_any_permission_async", fake_has_any)
    monkeypatch.setattr(assistant_ep, "get_default_operator", lambda: SimpleNamespace(user_id=3))

    dispatched = []

    async def fake_dispatch(*args, **kwargs):
        dispatched.append(args)

    monkeypatch.setattr(assistant_ep.chat_manager, "dispatch_client", fake_dispatch)

    ws = _FakeWebSocket()
    await assistant_ep.chat(websocket=ws, assistant_id=ASSISTANT_ID)

    assert ws.closed == (1008, str(OpenApiNotFoundError.Code))
    assert dispatched == []
