"""F078 T-17: invoke orchestration, permission/state gates and run trace."""

from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import StaticPool
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from bisheng.common.errcode.managed_agent import ManagedAgentUnavailableError
from bisheng.common.errcode.open_api import OpenApiNotFoundError
from bisheng.core.database.tenant_filter import register_tenant_filter_events
from bisheng.managed_agent.domain.models import dao as managed_dao
from bisheng.managed_agent.domain.models.managed_agent import ManagedAgent
from bisheng.managed_agent.domain.models.run import ManagedAgentRun
from bisheng.managed_agent.domain.services import invoke_service
from bisheng.managed_agent.domain.services.adapters.base import AgentResult

TENANT_ID = 7
USER = SimpleNamespace(user_id=42, is_admin=lambda: True)


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
                sync_conn, tables=[ManagedAgent.__table__, ManagedAgentRun.__table__]
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


def _agent(status="online"):
    return ManagedAgent(
        id=1,
        tenant_id=TENANT_ID,
        name="crm",
        source_kind="high_code",
        protocol="custom_http",
        status=status,
        endpoint="https://agents.example.com/v1",
        endpoint_hash="a" * 64,
        user_id=42,
    )


class _Adapter:
    def __init__(self, error=None):
        self.error = error

    async def invoke(self, agent, payload, session_id, caller):
        if self.error:
            raise self.error
        return AgentResult(run_id="run-1", output={"ok": True}, usage={"total_tokens": 1})


@pytest.fixture(autouse=True)
def _patch_deps(monkeypatch):
    async def _allowed(*args, **kwargs):
        return True

    async def _rate(*args, **kwargs):
        return None

    async def _audit(*args, **kwargs):
        return None

    monkeypatch.setattr(invoke_service.PermissionService, "check", _allowed)
    monkeypatch.setattr(invoke_service, "consume_rate", _rate)
    monkeypatch.setattr(invoke_service, "_audit_invoke", _audit)
    yield


async def test_success_returns_output_and_writes_run(patched_db, monkeypatch):
    async with AsyncSession(bind=patched_db, expire_on_commit=False) as session:
        session.add(_agent())
        await session.commit()
    monkeypatch.setattr(invoke_service, "get_adapter", lambda agent: _Adapter())

    result = await invoke_service.invoke(USER, 1, {"query": "hi"}, caller_type="api_key")

    assert result["run_id"] == "run-1"
    assert result["output"] == {"ok": True}
    async with AsyncSession(bind=patched_db, expire_on_commit=False) as session:
        rows = (await session.exec(select(ManagedAgentRun))).all()
    assert len(rows) == 1
    assert rows[0].status == "success"
    assert rows[0].agent_name_snapshot == "crm"


async def test_offline_agent_is_12105(patched_db, monkeypatch):
    async with AsyncSession(bind=patched_db, expire_on_commit=False) as session:
        session.add(_agent(status="offline"))
        await session.commit()
    monkeypatch.setattr(invoke_service, "get_adapter", lambda agent: _Adapter())

    with pytest.raises(ManagedAgentUnavailableError):
        await invoke_service.invoke(USER, 1, {"query": "hi"})


async def test_permission_denied_is_11809(patched_db, monkeypatch):
    async with AsyncSession(bind=patched_db, expire_on_commit=False) as session:
        session.add(_agent())
        await session.commit()

    async def _denied(*args, **kwargs):
        return False

    monkeypatch.setattr(invoke_service.PermissionService, "check", _denied)
    with pytest.raises(OpenApiNotFoundError):
        await invoke_service.invoke(USER, 1, {"query": "hi"})


async def test_adapter_error_still_writes_failed_run(patched_db, monkeypatch):
    async with AsyncSession(bind=patched_db, expire_on_commit=False) as session:
        session.add(_agent())
        await session.commit()
    from bisheng.common.errcode.managed_agent import ManagedAgentUpstreamError

    monkeypatch.setattr(
        invoke_service,
        "get_adapter",
        lambda agent: _Adapter(ManagedAgentUpstreamError(msg="boom")),
    )
    with pytest.raises(ManagedAgentUpstreamError):
        await invoke_service.invoke(USER, 1, {"query": "hi"})

    async with AsyncSession(bind=patched_db, expire_on_commit=False) as session:
        rows = (await session.exec(select(ManagedAgentRun))).all()
    assert rows[0].status == "failed"
    assert rows[0].error_code == 12106
