"""F078 T-03：三张纳管表的租户回填、CAS 与调用留痕。

覆盖：
- AC-01/AC-02/AC-05/AC-07/AC-08/AC-10/AC-11/AC-27/AC-43/AC-60/AC-65
- design §4.2 的字段约定；§5 坑 6/7/9（租户登记、strict 读、bulk 写手写
  tenant_id、禁 RETURNING）
- 回滚说明：三张表均可 DROP；测试使用内存 SQLite，不触碰真实库。
"""

from contextlib import asynccontextmanager
from datetime import datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import StaticPool
from sqlmodel.ext.asyncio.session import AsyncSession

from bisheng.core.database.tenant_filter import register_tenant_filter_events
from bisheng.managed_agent.domain.models import dao as managed_dao
from bisheng.managed_agent.domain.models.dao import (
    ManagedAgentDao,
    ManagedAgentRunDao,
    ManagedAgentTaxonomyDao,
)
from bisheng.managed_agent.domain.models.managed_agent import (
    AgentProtocol,
    AgentStatus,
    CredentialType,
    HealthMethod,
    HealthStatus,
    ManagedAgent,
    SourceKind,
)
from bisheng.managed_agent.domain.models.run import (
    CallerType,
    ManagedAgentRun,
    ManagedRunStatus,
)
from bisheng.managed_agent.domain.models.taxonomy import (
    ManagedAgentTaxonomy,
    TaxonomyKind,
)

TABLES = [ManagedAgent.__table__, ManagedAgentTaxonomy.__table__, ManagedAgentRun.__table__]
TENANT_ID = 7


@pytest.fixture()
async def engine():
    engine = create_async_engine(
        "sqlite+aiosqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(lambda sync_conn: ManagedAgent.metadata.create_all(sync_conn, tables=TABLES))
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


def _agent(**overrides) -> ManagedAgent:
    base = {
        "name": "crm-assistant",
        "source_kind": SourceKind.HIGH_CODE.value,
        "protocol": AgentProtocol.OPENAI_COMPATIBLE.value,
        "endpoint": "https://agents.example.com/v1",
        "endpoint_hash": "a" * 64,
        "model_name": "crm-v1",
        "user_id": 42,
    }
    base.update(overrides)
    return ManagedAgent(**base)


async def test_tenant_autofill_and_defaults(patched_db):
    row = await ManagedAgentDao.acreate(_agent())

    assert row.tenant_id == TENANT_ID
    assert row.status == AgentStatus.DRAFT.value
    assert row.health_status == HealthStatus.UNKNOWN.value
    assert row.health_method == HealthMethod.TEST_INVOKE.value
    assert row.credential_type == CredentialType.NONE.value
    assert row.has_credential is False
    assert row.is_callable is False


async def test_status_cas_and_tenant_scoped_writes(patched_db):
    row = await ManagedAgentDao.acreate(_agent())

    assert (
        await ManagedAgentDao.atransition(
            row.id, tenant_id=TENANT_ID, from_status=AgentStatus.DRAFT.value, to_status=AgentStatus.ONLINE.value
        )
        == 1
    )
    # 同一 from_status 再迁移一次必须失败（CAS）
    assert (
        await ManagedAgentDao.atransition(
            row.id, tenant_id=TENANT_ID, from_status=AgentStatus.DRAFT.value, to_status=AgentStatus.ONLINE.value
        )
        == 0
    )

    assert await ManagedAgentDao.aupdate_fields(row.id, {"version": "v2"}, tenant_id=999) == 0
    assert await ManagedAgentDao.aupdate_fields(row.id, {"version": "v2"}, tenant_id=TENANT_ID) == 1

    assert await ManagedAgentDao.adelete(row.id, tenant_id=999) == 0
    assert await ManagedAgentDao.adelete(row.id, tenant_id=TENANT_ID) == 1


async def test_taxonomy_unique_code_and_enabled_filter(patched_db):
    scenario = await ManagedAgentTaxonomyDao.acreate(
        ManagedAgentTaxonomy(
            kind=TaxonomyKind.SCENARIO.value,
            code="customer-service",
            name_zh="客户服务",
            name_en="Customer service",
            sort_order=1,
        )
    )
    await ManagedAgentTaxonomyDao.acreate(
        ManagedAgentTaxonomy(
            kind=TaxonomyKind.INDUSTRY.value,
            code="banking",
            name_zh="银行",
            is_enabled=False,
        )
    )

    assert scenario.tenant_id == TENANT_ID
    assert (await ManagedAgentTaxonomyDao.aget_by_code("scenario", "customer-service")).name_zh == "客户服务"
    enabled = await ManagedAgentTaxonomyDao.alist(enabled_only=True)
    assert [item.code for item in enabled] == ["customer-service"]
    all_rows = await ManagedAgentTaxonomyDao.alist()
    assert {item.kind for item in all_rows} == {"scenario", "industry"}


async def test_run_snapshots_and_stats_exclude_health_check(patched_db):
    now = datetime.now()
    await ManagedAgentRunDao.acreate(
        ManagedAgentRun(
            agent_id=1,
            agent_name_snapshot="crm-assistant",
            source_kind_snapshot=SourceKind.HIGH_CODE.value,
            protocol_snapshot=AgentProtocol.OPENAI_COMPATIBLE.value,
            run_id="run-1",
            create_time=now,
            caller_type=CallerType.API_KEY.value,
            status=ManagedRunStatus.SUCCESS.value,
            latency_ms=100,
        )
    )
    await ManagedAgentRunDao.acreate(
        ManagedAgentRun(
            agent_id=1,
            run_id="run-2",
            create_time=now,
            caller_type=CallerType.SCHEDULE.value,
            status=ManagedRunStatus.FAILED.value,
            latency_ms=300,
            error_code=12106,
        )
    )
    await ManagedAgentRunDao.acreate(
        ManagedAgentRun(
            agent_id=1,
            run_id="run-health",
            create_time=now,
            caller_type=CallerType.HEALTH_CHECK.value,
            status=ManagedRunStatus.FAILED.value,
            latency_ms=999,
        )
    )

    stats = await ManagedAgentRunDao.astats(agent_id=1, start_time=now - timedelta(minutes=1))
    assert stats["calls"] == 2
    assert stats["success_count"] == 1
    assert stats["failure_count"] == 1
    assert stats["success_rate"] == 0.5
    assert stats["avg_latency_ms"] == 200.0

    rows, total = await ManagedAgentRunDao.alist(agent_id=1, start_time=now - timedelta(minutes=1))
    assert total == 2
    assert {row.run_id for row in rows} == {"run-1", "run-2"}
