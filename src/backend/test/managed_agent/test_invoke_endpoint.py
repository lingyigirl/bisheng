"""F078 T-18: v2 invoke endpoint whitelist and delegation."""

from types import SimpleNamespace

import pytest

from bisheng.common.errcode.managed_agent import ManagedAgentInvokePayloadInvalidError
from bisheng.common.errcode.open_api import OpenApiKeyMissingError
from bisheng.managed_agent.api.endpoints import invoke as invoke_endpoint

OWNER = SimpleNamespace(user_id=42, is_admin=lambda: True)
PRINCIPAL = SimpleNamespace(owner_payload=OWNER, service_account_id=5)
REQUEST = SimpleNamespace()


@pytest.fixture(autouse=True)
def _principal(monkeypatch):
    monkeypatch.setattr(invoke_endpoint, "get_open_api_principal", lambda: PRINCIPAL)
    yield


async def test_extra_fields_are_12114():
    with pytest.raises(ManagedAgentInvokePayloadInvalidError):
        await invoke_endpoint.invoke_managed_agent(REQUEST, {"agent_id": 1, "input": {}, "user_id": 9})


async def test_agent_id_must_be_integer():
    with pytest.raises(ManagedAgentInvokePayloadInvalidError):
        await invoke_endpoint.invoke_managed_agent(REQUEST, {"agent_id": "1", "input": {}})


async def test_input_required():
    with pytest.raises(ManagedAgentInvokePayloadInvalidError):
        await invoke_endpoint.invoke_managed_agent(REQUEST, {"agent_id": 1})


async def test_missing_principal_is_401(monkeypatch):
    monkeypatch.setattr(invoke_endpoint, "get_open_api_principal", lambda: None)
    with pytest.raises(OpenApiKeyMissingError):
        await invoke_endpoint.invoke_managed_agent(REQUEST, {"agent_id": 1, "input": {}})


async def test_success_delegates_to_service(monkeypatch):
    calls = {}

    async def _invoke(user, agent_id, payload, **kwargs):
        calls["user"] = user
        calls["agent_id"] = agent_id
        calls["payload"] = payload
        calls["kwargs"] = kwargs
        return {"run_id": "run-1", "output": "ok"}

    monkeypatch.setattr(invoke_endpoint.invoke_service, "invoke", _invoke)
    resp = await invoke_endpoint.invoke_managed_agent(
        REQUEST, {"agent_id": 1, "input": {"query": "hi"}, "session_id": "s-1"}
    )
    assert resp.status_code == 200
    assert resp.data["output"] == "ok"
    assert calls["kwargs"]["service_account_id"] == 5
    assert calls["kwargs"]["caller_type"] == "api_key"
