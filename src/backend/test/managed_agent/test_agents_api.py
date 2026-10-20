"""F078 T-14: v1 management route matrix and basic delegation."""

from types import SimpleNamespace

from fastapi import Request

from bisheng.managed_agent.api.endpoints import agents
from bisheng.managed_agent.domain.schemas.agent import ManagedAgentCreateReq
from bisheng.managed_agent.domain.services import registry_service

USER = SimpleNamespace(user_id=42, is_admin=lambda: True, has_tenant_admin=None)


def _request() -> Request:
    return Request({"type": "http", "headers": [(b"accept-language", b"zh-Hans")]})


def _agent():
    return SimpleNamespace(
        id=1,
        name="crm",
        description=None,
        source_kind="high_code",
        protocol="openai_compatible",
        status="draft",
        version=None,
        native_app_id=None,
        native_app_type=None,
        endpoint="https://agents.example.com/v1",
        model_name="crm",
        scenario_code=None,
        industry_code=None,
        health_status="unknown",
        health_method="test_invoke",
        last_health_check_at=None,
        last_call_at=None,
        last_run_status=None,
        last_latency_ms=None,
        credential_type="none",
        has_credential=False,
        create_time=None,
        update_time=None,
    )


async def test_create_endpoint_delegates_and_masks(monkeypatch):
    async def _create(user, req):
        return _agent()

    async def _taxonomies(user):
        return []

    monkeypatch.setattr(registry_service, "create_agent", _create)
    monkeypatch.setattr(agents.classification_service, "list_taxonomies", _taxonomies)

    async def _no_tags(ids):
        return {}

    monkeypatch.setattr(agents, "_tag_meta_map", _no_tags)

    resp = await agents.create_agent(
        ManagedAgentCreateReq(
            name="crm",
            source_kind="high_code",
            protocol="openai_compatible",
            endpoint="https://agents.example.com/v1",
        ),
        _request(),
        USER,
    )
    assert resp.status_code == 200
    assert resp.data.name == "crm"
    assert not hasattr(resp.data, "credential_ciphertext")


async def test_catalog_route_is_registered_before_dynamic_route():
    paths = [route.path for route in agents.router.routes]
    assert "/managed-agents/catalog" in paths
    assert "/managed-agents/{agent_id}" in paths
    assert paths.index("/managed-agents/catalog") < paths.index("/managed-agents/{agent_id}")
