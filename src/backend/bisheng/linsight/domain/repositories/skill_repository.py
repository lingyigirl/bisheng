"""Tenant-private Skill persistence, including compare-and-swap publication."""

from datetime import datetime

from sqlalchemy import update
from sqlmodel import col, select

from bisheng.core.context.tenant import DEFAULT_TENANT_ID, get_current_tenant_id, strict_tenant_filter
from bisheng.core.database import get_async_db_session
from bisheng.linsight.domain.models.linsight_skill import LinsightSkill, LinsightSkillDao


class SkillRepository:
    def __init__(self, dao=None):
        self.dao = dao or LinsightSkillDao

    async def publish(self, previous, values):
        tid = get_current_tenant_id() or DEFAULT_TENANT_ID
        # Bulk writes are not covered by the SELECT-only tenant interceptor.
        statement = (
            update(LinsightSkill)
            .where(
                col(LinsightSkill.id) == previous.id,
                col(LinsightSkill.tenant_id) == tid,
                col(LinsightSkill.object_path) == previous.object_path,
                col(LinsightSkill.content_hash) == previous.content_hash,
            )
            .values(**values, update_time=datetime.now())
        )
        async with get_async_db_session() as session:
            result = await session.execute(statement)
            await session.commit()
            return result.rowcount == 1

    async def page_after(self, after=0, limit=100):
        with strict_tenant_filter():
            async with get_async_db_session() as session:
                result = await session.exec(
                    select(LinsightSkill).where(LinsightSkill.id > after).order_by(LinsightSkill.id).limit(limit)
                )
                return list(result.all())

    async def enabled(self):
        return await self.dao.list_enabled()

    async def get(self, name):
        return await self.dao.get_by_name(name)

    async def by_display_name(self, name):
        return await self.dao.get_by_display_name(name)

    async def create(self, skill):
        return await self.dao.create(skill)

    async def get_page(self, **kwargs):
        return await self.dao.get_page(**kwargs)

    async def set_enabled(self, name, enabled):
        return await self.dao.set_enabled(name, enabled)

    async def delete(self, name):
        return await self.dao.delete_by_name(name)
