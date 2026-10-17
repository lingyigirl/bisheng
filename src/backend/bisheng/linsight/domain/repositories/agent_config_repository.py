"""Read existing resources through their established tenant-aware accessors."""

from bisheng.linsight.domain.models.linsight_session_version import LinsightSessionVersionDao
from bisheng.linsight.domain.models.linsight_skill import LinsightSkillDao
from bisheng.tool.domain.models.gpts_tools import GptsToolsDao


class AgentConfigRepository:
    async def tools(self, ids: list[int]):
        return await GptsToolsDao.aget_list_by_ids(ids) if ids else []

    async def tool_types(self, ids: list[int]):
        return await GptsToolsDao.aget_all_tool_type(ids) if ids else []

    async def enabled_skills(self):
        return await LinsightSkillDao.list_enabled()

    async def task(self, task_id: str):
        return await LinsightSessionVersionDao.get_by_id(task_id)
