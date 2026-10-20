"""F078 T-11: registry lifecycle, CAS, delete protection and tenant reads."""

from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import StaticPool
from sqlmodel.ext.asyncio.session import AsyncSession

from bisheng.common.errcode.managed_agent import (
    ManagedAgentDuplicateError,
    ManagedAgentInUseError,
    ManagedAgentUnavailableError,
)
from bisheng.core.database.tenant_filter import register_tenant_filter_events
from bisheng.managed_agent.domain.models import dao as managed_dao
from bisheng.managed_agent.domain.models.managed_agent import ManagedAgent
from bisheng.managed_agent.domain.models.taxonomy import ManagedAgentTaxonomy as Taxonomy
from bisheng.managed_agent.domain.schemas.agent import (
    CredentialReq,
    ManagedAgentCreateReq,
    ManagedAgentUpdateReq,
)
from bisheng.managed_agent.domain.services import registry_service

TENANT_ID = 7
USER = SimpleNamespace(user_id=42, is_admin=lambda: True, has_tenant_admin=AsyncMock(return_value=True))


@pytest.fixture()
async def engine():
    engine = create_async_engine(
        "sqlite+aiosqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(
            lambda sync_conn: ManagedAgent.metadata.create_all(
                sync_conn, tables=[ManagedAgent.__table__, Taxonomy.__table__]
            )
        )
    register_tenant_filter_events()
    yield engine
    await engine.dispose()


@pytest.fixture()
async def patched_db(engine, monkeypatch):
    @asynccontextmanager
    async def _factory():
        async with AsyncSession(bind=engine, expire_on_commit=False) as session:
            yield session

    monkeypatch.setattr(managed_dao, "get_async_db_session", _factory)
    yield engine


@pytest.fixture(autouse=True)
async def _tenant_ctx():
    from bisheng.core.context.tenant import set_current_tenant_id

    set_current_tenant_id(TENANT_ID)
    yield TENANT_ID
    set_current_tenant_id(None)


@pytest.fixture(autouse=True)
def _patch_side_effects(monkeypatch):
    async def _fake_validate(url):
        return url

    async def _fake_owner(*args, **kwargs):
        return None

    async def _fake_audit(*args, **kwargs):
        return None

    monkeypatch.setattr(registry_service, "_validate_endpoint", _fake_validate)
    monkeypatch.setattr(registry_service.OwnerService, "write_owner_tuple", _fake_owner)
    monkeypatch.setattr(registry_service, "_audit", _fake_audit)
    yield


def _create_req(**overrides) -> ManagedAgentCreateReq:
    base = {
        "name": "crm-assistant",
        "description": "CRM",
        "source_kind": "high_code",
        "protocol": "openai_compatible",
        "endpoint": "https://agents.example.com/v1",
        "model_name": "crm-v1",
        "credential": CredentialReq(auth_type="bearer", secret="secret"),
    }
    base.update(overrides)
    return ManagedAgentCreateReq(**base)


async def test_create_external_agent_draft_with_masked_credential(patched_db):
    agent = await registry_service.create_agent(USER, _create_req())
    assert agent.id is not None
    assert agent.tenant_id == TENANT_ID
    assert agent.status == "draft"
    assert agent.endpoint_hash
    assert agent.has_credential is True
    assert "secret" not in (agent.credential_ciphertext or "")

    item = registry_service.to_item(agent)
    assert item["credential_mask"] == "******"
    assert "credential_ciphertext" not in item


async def test_duplicate_name_rejected(patched_db):
    await registry_service.create_agent(USER, _create_req())
    with pytest.raises(ManagedAgentDuplicateError):
        await registry_service.create_agent(USER, _create_req(endpoint="https://other.example.com/v1"))


async def test_native_projection_uses_native_hash(patched_db, monkeypatch):
    async def _exists(app_id, app_type):
        return True

    monkeypatch.setattr(registry_service, "_native_app_exists", _exists)
    agent = await registry_service.create_agent(
        USER,
        ManagedAgentCreateReq(
            name="native-flow",
            source_kind="native",
            protocol="native",
            native_app_id="flow-1",
            native_app_type=10,
        ),
    )
    assert agent.endpoint is None
    assert agent.native_app_id == "flow-1"
    assert agent.endpoint_hash == registry_service.native_endpoint_hash(10, "flow-1")


async def test_update_keeps_credential_when_secret_is_empty(patched_db):
    agent = await registry_service.create_agent(USER, _create_req())
    old_cipher = agent.credential_ciphertext

    updated = await registry_service.update_agent(
        USER,
        agent.id,
        ManagedAgentUpdateReq(name="renamed", credential=CredentialReq(auth_type="bearer", secret="")),
    )
    assert updated.name == "renamed"
    assert updated.credential_ciphertext == old_cipher
    assert updated.has_credential is True


async def test_transitions_are_cas(patched_db):
    agent = await registry_service.create_agent(USER, _create_req())
    online = await registry_service.transition(USER, agent.id, "publish")
    assert online.status == "online"
    with pytest.raises(ManagedAgentUnavailableError):
        await registry_service.transition(USER, agent.id, "publish")
    offline = await registry_service.transition(USER, agent.id, "offline")
    assert offline.status == "offline"
    disabled = await registry_service.transition(USER, agent.id, "disable")
    assert disabled.status == "disabled"


async def test_delete_is_blocked_by_enabled_scheduled_tasks(patched_db, monkeypatch):
    agent = await registry_service.create_agent(USER, _create_req())

    async def _count(app_id, app_type):
        assert app_id == str(agent.id)
        assert app_type == 100
        return 2

    from bisheng.scheduled_task.domain.models.dao import ScheduledTaskDao

    monkeypatch.setattr(ScheduledTaskDao, "acount_enabled_by_app", _count)
    with pytest.raises(ManagedAgentInUseError):
        await registry_service.delete_agent(USER, agent.id)

    async def _zero(app_id, app_type):
        return 0

    monkeypatch.setattr(ScheduledTaskDao, "acount_enabled_by_app", _zero)
    await registry_service.delete_agent(USER, agent.id)
    assert await registry_service.ManagedAgentDao.aget(agent.id) is None


async def test_create_attaches_tags(patched_db, monkeypatch):
    attach = AsyncMock()
    monkeypatch.setattr(registry_service.TagDao, "aupdate_resource_tags", attach)
    await registry_service.create_agent(USER, _create_req(tags=[1, 2]))
    attach.assert_awaited_once()
    assert attach.call_args.args[0] == [1, 2]


async def test_to_item_includes_tag_ids_and_names(patched_db):
    agent = await registry_service.create_agent(USER, _create_req())
    item = registry_service.to_item(agent, tag_ids=[10], tag_names=["banking"])
    assert item["tags"] == [10]
    assert item["tag_names"] == ["banking"]


async def test_tag_meta_map_batches_ids_and_names(monkeypatch):
    async def _links(resource_ids, resource_type):
        assert resource_ids == ["1", "2"]
        return {"1": [10, 11], "2": [11]}

    async def _tags(tag_ids):
        assert tag_ids == [10, 11]
        return [SimpleNamespace(id=10, name="banking"), SimpleNamespace(id=11, name="retail")]

    monkeypatch.setattr(registry_service.TagDao, "aget_resource_tag_ids_batch", _links)
    monkeypatch.setattr(registry_service.TagDao, "aget_tags_by_ids", _tags)
    assert await registry_service.tag_meta_map([1, 2]) == {
        1: ([10, 11], ["banking", "retail"]),
        2: ([11], ["retail"]),
    }
    assert await registry_service.tag_names_map([1, 2]) == {1: ["banking", "retail"], 2: ["retail"]}


async def test_admin_list_returns_rows(patched_db):
    await registry_service.create_agent(USER, _create_req())
    rows, total = await registry_service.list_agents(USER)
    assert total == 1
    assert rows[0].name == "crm-assistant"
