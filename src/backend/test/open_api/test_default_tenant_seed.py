"""F077 部署便利：默认租户策略的启动播种（`open_api.default_tenant_enforce_keys`）。

口径（见播种函数 docstring）：
- 缺行且配置为 true → 写入一行 + 记一条 system 审计；
- **已有行不覆盖**（DB 是事实源，运维手动关掉不会因重启被改回来）；
- 配置为 false 且缺行 → 什么都不做（保持 fail-closed）。
"""

from contextlib import asynccontextmanager

import pytest
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import StaticPool
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from bisheng.core.context.tenant import DEFAULT_TENANT_ID
from bisheng.core.database.tenant_filter import register_tenant_filter_events
from bisheng.open_api.domain.models import dao as open_api_dao
from bisheng.open_api.domain.models.dao import OpenApiTenantSettingDao
from bisheng.open_api.domain.models.open_api_tenant_setting import OpenApiTenantSetting
from bisheng.open_api.domain.services import tenant_setting_service as service

TABLES = [OpenApiTenantSetting.__table__]


@pytest.fixture()
async def engine():
    eng = create_async_engine("sqlite+aiosqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    async with eng.begin() as conn:
        await conn.run_sync(lambda c: OpenApiTenantSetting.metadata.create_all(c, tables=TABLES))
    register_tenant_filter_events()
    yield eng
    await eng.dispose()


@pytest.fixture()
async def patched(engine, monkeypatch):
    @asynccontextmanager
    async def _factory():
        async with AsyncSession(bind=engine, expire_on_commit=False) as session:
            yield session

    async def _noop(*args, **kwargs):
        return None

    audits: list[dict] = []

    async def _audit(**kwargs):
        audits.append(kwargs)

    monkeypatch.setattr(open_api_dao, "get_async_db_session", _factory)
    monkeypatch.setattr(service, "_cache_get_policy", _noop)
    monkeypatch.setattr(service, "_cache_set_policy", _noop)
    monkeypatch.setattr(service, "_cache_clear_policy", _noop)
    monkeypatch.setattr("bisheng.database.models.audit_log.AuditLogDao.ainsert_v2", _audit)
    return {"engine": engine, "audits": audits}


async def _row(engine) -> OpenApiTenantSetting | None:
    async with AsyncSession(bind=engine, expire_on_commit=False) as session:
        return (
            (
                await session.execute(
                    select(OpenApiTenantSetting).where(OpenApiTenantSetting.tenant_id == DEFAULT_TENANT_ID)
                )
            )
            .scalars()
            .first()
        )


async def test_seed_creates_row_and_audits(patched):
    assert await service.seed_default_tenant_policy(True) is True

    row = await _row(patched["engine"])
    assert row is not None and row.enforce_keys is True
    assert len(patched["audits"]) == 1
    assert patched["audits"][0]["action"] == "open_api.settings.update"
    assert patched["audits"][0]["operator_id"] == 0, "系统播种以 system 身份留痕"


async def test_seed_never_overwrites_an_existing_row(patched):
    # 先由"运维"手动关掉（已有行 = false）
    await OpenApiTenantSettingDao.aupsert(DEFAULT_TENANT_ID, enforce_keys=False)

    assert await service.seed_default_tenant_policy(True) is False
    row = await _row(patched["engine"])
    assert row is not None and row.enforce_keys is False, "已有行不得被启动配置覆盖"
    assert patched["audits"] == []


async def test_seed_off_leaves_missing_row_missing(patched):
    assert await service.seed_default_tenant_policy(False) is False
    assert await _row(patched["engine"]) is None, "缺行=未启用（fail-closed）"
