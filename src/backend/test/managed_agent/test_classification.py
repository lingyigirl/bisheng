"""F078 T-12: taxonomy CRUD, uniqueness, delete guard and localization."""

from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import StaticPool
from sqlmodel.ext.asyncio.session import AsyncSession

from bisheng.common.errcode.managed_agent import (
    ManagedAgentDuplicateError,
    ManagedAgentTaxonomyInvalidError,
)
from bisheng.core.database.tenant_filter import register_tenant_filter_events
from bisheng.managed_agent.domain.models import dao as managed_dao
from bisheng.managed_agent.domain.models.managed_agent import ManagedAgent
from bisheng.managed_agent.domain.models.taxonomy import ManagedAgentTaxonomy
from bisheng.managed_agent.domain.schemas.agent import TaxonomyCreateReq, TaxonomyUpdateReq
from bisheng.managed_agent.domain.services import classification_service

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
                sync_conn, tables=[ManagedAgent.__table__, ManagedAgentTaxonomy.__table__]
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

    set_current_tenant_id(7)
    yield 7
    set_current_tenant_id(None)


async def test_create_list_update_delete(patched_db):
    row = await classification_service.create_taxonomy(
        USER,
        TaxonomyCreateReq(kind="scenario", code="customer-service", name_zh="客户服务", name_en="Customer service"),
    )
    assert row.id is not None
    rows = await classification_service.list_taxonomies(USER, kind="scenario")
    assert [item.code for item in rows] == ["customer-service"]
    assert classification_service.localized_name(row, "en-US") == "Customer service"

    updated = await classification_service.update_taxonomy(
        USER, row.id, TaxonomyUpdateReq(name_ja="顧客サービス", sort_order=5)
    )
    assert updated.name_ja == "顧客サービス"
    assert updated.sort_order == 5

    await classification_service.delete_taxonomy(USER, row.id)
    assert await classification_service.list_taxonomies(USER) == []


async def test_duplicate_code_rejected(patched_db):
    req = TaxonomyCreateReq(kind="scenario", code="banking", name_zh="银行")
    await classification_service.create_taxonomy(USER, req)
    with pytest.raises(ManagedAgentDuplicateError):
        await classification_service.create_taxonomy(USER, req)


async def test_invalid_code_rejected(patched_db):
    with pytest.raises(ManagedAgentTaxonomyInvalidError):
        await classification_service.create_taxonomy(
            USER, TaxonomyCreateReq(kind="scenario", code="bad code!", name_zh="坏")
        )


async def test_delete_referenced_code_is_blocked(patched_db, monkeypatch):
    row = await classification_service.create_taxonomy(
        USER, TaxonomyCreateReq(kind="scenario", code="banking", name_zh="银行")
    )

    async def _one(kind, code):
        return 1

    monkeypatch.setattr(managed_dao.ManagedAgentDao, "acount_by_taxonomy", _one)
    with pytest.raises(ManagedAgentTaxonomyInvalidError):
        await classification_service.delete_taxonomy(USER, row.id)


async def test_disabled_code_still_lists_with_enabled_only_false(patched_db):
    row = await classification_service.create_taxonomy(
        USER, TaxonomyCreateReq(kind="industry", code="banking", name_zh="银行", is_enabled=False)
    )
    assert await classification_service.list_taxonomies(USER, enabled_only=True) == []
    assert [item.id for item in await classification_service.list_taxonomies(USER)] == [row.id]
