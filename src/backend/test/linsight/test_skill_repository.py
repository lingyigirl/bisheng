"""Real SQL conditional publication cannot cross tenants or overwrite new versions."""

from contextlib import asynccontextmanager

import pytest
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import StaticPool
from sqlmodel.ext.asyncio.session import AsyncSession

from bisheng.core.context.tenant import current_tenant_id, set_current_tenant_id
from bisheng.linsight.domain.models.linsight_skill import LinsightSkill
from bisheng.linsight.domain.repositories import skill_repository as module


@pytest.fixture
async def repository(monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)
    async with engine.begin() as conn:
        await conn.run_sync(LinsightSkill.__table__.create)

    @asynccontextmanager
    async def session():
        async with AsyncSession(engine, expire_on_commit=False) as db:
            yield db

    monkeypatch.setattr(module, "get_async_db_session", session)
    token = set_current_tenant_id(1)
    row = LinsightSkill(
        name="demo", display_name="Demo", description="Synthetic", object_path="data/skills/1/demo", tenant_id=1
    )
    async with session() as db:
        db.add(row)
        await db.commit()
    yield module.SkillRepository(), row
    current_tenant_id.reset(token)
    await engine.dispose()


async def test_publish_cas_and_tenant_boundary(repository):
    repo, row = repository
    token = set_current_tenant_id(2)
    assert not await repo.publish(row, {"content_hash": "a" * 64, "object_path": "first"})
    current_tenant_id.reset(token)
    assert await repo.publish(row, {"content_hash": "a" * 64, "object_path": "first"})
    assert not await repo.publish(row, {"content_hash": "b" * 64, "object_path": "second"})


async def test_publish_does_not_resurrect_deleted_row(repository):
    repo, row = repository
    async with module.get_async_db_session() as db:
        await db.delete(await db.get(LinsightSkill, row.id))
        await db.commit()
    assert not await repo.publish(row, {"content_hash": "a" * 64})
