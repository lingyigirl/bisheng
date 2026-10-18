"""Read existing resources through their established tenant-aware accessors."""

from sqlmodel import select

from bisheng.core.context.tenant import strict_tenant_filter
from bisheng.core.database import get_async_db_session
from bisheng.linsight.domain.models.linsight_session_version import LinsightSessionVersionDao
from bisheng.linsight.domain.models.linsight_skill import LinsightSkill, LinsightSkillDao
from bisheng.tool.domain.models.gpts_tools import GptsTools, GptsToolsDao, GptsToolsType


class AgentConfigRepository:
    async def runtime_tools(self, ids: list[int]):
        if not ids:
            return []
        async with get_async_db_session() as session:
            result = await session.execute(
                select(
                    GptsTools.id,
                    GptsTools.type,
                    GptsTools.is_delete,
                    GptsToolsType.is_delete.label("group_deleted"),
                    GptsToolsType.is_preset,
                )
                .join(GptsToolsType, GptsTools.type == GptsToolsType.id)
                .where(GptsTools.id.in_(ids))
            )
            return result.all()

    async def enabled_skill_names(self, names: list[str]):
        if not names:
            return set()
        with strict_tenant_filter():
            async with get_async_db_session() as session:
                result = await session.execute(
                    select(LinsightSkill.name).where(LinsightSkill.name.in_(names), LinsightSkill.enabled == 1)
                )
                return set(result.scalars())

    async def tools(self, ids: list[int]):
        return await GptsToolsDao.aget_list_by_ids(ids) if ids else []

    async def tool_types(self, ids: list[int]):
        return await GptsToolsDao.aget_all_tool_type(ids) if ids else []

    async def enabled_skills(self):
        return await LinsightSkillDao.list_enabled()

    async def task(self, task_id: str):
        return await LinsightSessionVersionDao.get_by_id(task_id)
