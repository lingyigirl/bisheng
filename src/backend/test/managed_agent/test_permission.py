"""F078 T-09a: PermissionService creator/tenant/list branches for managed_agent."""

from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import StaticPool
from sqlmodel.ext.asyncio.session import AsyncSession

from bisheng.core.database.tenant_filter import register_tenant_filter_events
from bisheng.managed_agent.domain.models.dao import ManagedAgentDao
from bisheng.managed_agent.domain.models.managed_agent import ManagedAgent
from bisheng.permission.domain.services.permission_service import PermissionService

TENANT_ID = 7


@pytest.fixture(autouse=True)
async def _tenant_ctx():
    from bisheng.core.context.tenant import set_current_tenant_id

    set_current_tenant_id(TENANT_ID)
    yield TENANT_ID
    set_current_tenant_id(None)


@pytest.fixture()
async def engine():
    engine = create_async_engine(
        "sqlite+aiosqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(
            lambda sync_conn: ManagedAgent.metadata.create_all(sync_conn, tables=[ManagedAgent.__table__])
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

    import bisheng.core.database as core_db

    monkeypatch.setattr(core_db, "get_async_db_session", _factory)
    yield engine


def _agent(agent_id: int, user_id: int = 42) -> ManagedAgent:
    return ManagedAgent(
        id=agent_id,
        tenant_id=TENANT_ID,
        name=f"agent-{agent_id}",
        source_kind="high_code",
        protocol="custom_http",
        endpoint=f"https://agents.example.com/v{agent_id}",
        endpoint_hash=f"{agent_id:064x}",
        user_id=user_id,
    )


def test_managed_agent_is_tenant_gated():
    assert "managed_agent" in PermissionService._TENANT_GATED_RESOURCE_TYPES


async def test_get_resource_creator(monkeypatch):
    async def _fake_bypass(agent_id):
        return SimpleNamespace(user_id=42)

    monkeypatch.setattr(ManagedAgentDao, "aget_bypass", _fake_bypass)
    assert await PermissionService._get_resource_creator("managed_agent", "1") == 42


async def test_resolve_resource_tenant(monkeypatch):
    async def _fake_bypass(agent_id):
        return SimpleNamespace(tenant_id=TENANT_ID)

    monkeypatch.setattr(ManagedAgentDao, "aget_bypass", _fake_bypass)
    assert await PermissionService._resolve_resource_tenant("managed_agent", "1") == TENANT_ID


async def test_list_helpers_include_managed_agents(patched_db, monkeypatch):
    async with AsyncSession(bind=patched_db, expire_on_commit=False) as session:
        session.add(_agent(1))
        session.add(_agent(2, user_id=99))
        await session.commit()

    ids = await PermissionService._resource_ids_by_creator_user_ids("managed_agent", {42})
    assert ids == ["1"]

    tenant_ids = await PermissionService._resource_ids_in_tenants("managed_agent", {TENANT_ID})
    assert set(tenant_ids) == {"1", "2"}

    mapping = await PermissionService._resource_tenant_map("managed_agent", ["1", "2"])
    assert mapping == {"1": TENANT_ID, "2": TENANT_ID}
