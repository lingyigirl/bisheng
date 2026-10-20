"""F078 T-19: health state transitions and cross-tenant beat scan."""

from contextlib import asynccontextmanager

import pytest
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import StaticPool
from sqlmodel.ext.asyncio.session import AsyncSession

from bisheng.core.database.tenant_filter import register_tenant_filter_events
from bisheng.managed_agent.domain.models import dao as managed_dao
from bisheng.managed_agent.domain.models.managed_agent import ManagedAgent
from bisheng.managed_agent.domain.services import health_service


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

    monkeypatch.setattr(managed_dao, "get_async_db_session", _factory)
    yield engine


@pytest.fixture(autouse=True)
async def _tenant_ctx():
    from bisheng.core.context.tenant import set_current_tenant_id

    set_current_tenant_id(None)
    yield
    set_current_tenant_id(None)


def _agent(agent_id, tenant_id, status="online"):
    return ManagedAgent(
        id=agent_id,
        tenant_id=tenant_id,
        name=f"agent-{agent_id}",
        source_kind="high_code",
        protocol="custom_http",
        status=status,
        endpoint=f"https://agents.example.com/v{agent_id}",
        endpoint_hash=f"{agent_id:064x}",
        user_id=42,
    )


async def test_apply_marks_healthy_and_resets_failures(patched_db):
    async with AsyncSession(bind=patched_db, expire_on_commit=False) as session:
        row = _agent(1, 7)
        row.health_status = "unhealthy"
        row.consecutive_health_failures = 2
        session.add(row)
        await session.commit()

    await health_service._apply(row, ok=True, latency_ms=12, detail="ok")
    updated = await managed_dao.ManagedAgentDao.aget_bypass(1)
    assert updated.health_status == "healthy"
    assert updated.consecutive_health_failures == 0


async def test_scan_once_checks_each_tenant_and_restores_context(patched_db, monkeypatch):
    async with AsyncSession(bind=patched_db, expire_on_commit=False) as session:
        session.add(_agent(1, 7))
        session.add(_agent(2, 8))
        await session.commit()

    seen_tenants = []

    async def _check(agent):
        from bisheng.core.context.tenant import get_current_tenant_id

        seen_tenants.append(get_current_tenant_id())
        return True, 5, "ok"

    async def _apply(agent, **kwargs):
        return None

    monkeypatch.setattr(health_service, "_check", _check)
    monkeypatch.setattr(health_service, "_apply", _apply)

    stats = await health_service.scan_once()
    assert stats == {"checked": 2, "failed": 0, "total": 2}
    assert sorted(seen_tenants) == [7, 8]
    from bisheng.core.context.tenant import get_current_tenant_id

    assert get_current_tenant_id() is None


async def test_failure_does_not_stop_the_round(patched_db, monkeypatch):
    async with AsyncSession(bind=patched_db, expire_on_commit=False) as session:
        session.add(_agent(1, 7))
        session.add(_agent(2, 8))
        await session.commit()

    calls = {"n": 0}

    async def _check(agent):
        calls["n"] += 1
        if agent.id == 1:
            raise RuntimeError("boom")
        return True, 5, "ok"

    async def _apply(agent, **kwargs):
        return None

    monkeypatch.setattr(health_service, "_check", _check)
    monkeypatch.setattr(health_service, "_apply", _apply)

    stats = await health_service.scan_once()
    assert calls["n"] == 2
    assert stats["total"] == 2
