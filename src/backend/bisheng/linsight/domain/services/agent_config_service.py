"""Resolve task collaborator snapshots without silently dropping resources."""

import asyncio
import hashlib

from bisheng.common.errcode.linsight import LinsightAgentConfigurationError
from bisheng.linsight.domain.repositories.agent_config_repository import AgentConfigRepository
from bisheng.linsight.domain.schemas.agent_config import AgentConfig
from bisheng.linsight.domain.services.skill_store import SkillStore
from bisheng.llm.domain.services.llm import LLMService
from bisheng.permission.domain.services.tool_permission_service import ToolPermissionService
from bisheng.tool.domain.const import ToolPresetType


def skill_digest(store: SkillStore, tenant_id: int, name: str) -> str:
    digest = hashlib.sha256()
    entries = store.list_files(tenant_id, name)
    if not any(entry["path"] == "SKILL.md" for entry in entries):
        raise LinsightAgentConfigurationError(msg="Skill bundle is missing")
    for entry in sorted(entries, key=lambda value: value["path"]):
        path = entry["path"]
        digest.update(path.encode())
        digest.update(b"\0")
        digest.update(store.read_bytes(tenant_id, name, path))
    return digest.hexdigest()


class AgentConfigService:
    def __init__(self, repository=None, store=None):
        self.repository = repository or AgentConfigRepository()
        self.store = store

    async def resolve(
        self,
        config: AgentConfig | None,
        user,
        tenant_id: int,
        main_model: str | None,
        *,
        include_skill_digests: bool = True,
    ):
        if config is None or not config.subagents:
            return None
        default = main_model or (await LLMService.get_workbench_llm(tenant_id=tenant_id)).linsight_default_model_id
        if not default:
            raise LinsightAgentConfigurationError(msg="No task model is configured")
        enabled = {skill.name for skill in await self.repository.enabled_skills()}
        snapshot = config.model_dump()
        digests = {}
        for member in snapshot["subagents"]:
            member["model_id"] = str(member["model_id"] or default)
            model = await LLMService.get_model_for_call(int(member["model_id"]))
            if not model.online or model.model_type != "llm":
                raise LinsightAgentConfigurationError(msg="Selected model is unavailable for chat")
            ids = member["tool_refs"] + member["connector_refs"]
            rows = await self.repository.tools(ids)
            if set(ids) != {row.id for row in rows if not row.is_delete}:
                raise LinsightAgentConfigurationError(msg="Selected tool is unavailable")
            groups = {row.id: row for row in await self.repository.tool_types(list({row.type for row in rows}))}
            for tool in rows:
                group = groups.get(tool.type)
                if group is None or group.is_delete:
                    raise LinsightAgentConfigurationError(msg="Selected connector is unavailable")
                if tool.id in member["connector_refs"] and group.is_preset == ToolPresetType.PRESET.value:
                    raise LinsightAgentConfigurationError(msg="A connector must reference an API or MCP action")
                if group.is_preset != ToolPresetType.PRESET.value:
                    allowed = await ToolPermissionService.has_any_permission_async(user, str(group.id), ["use_tool"])
                    if not allowed:
                        raise LinsightAgentConfigurationError(msg="Selected tool is not authorized")
            if not set(member["skill_names"]).issubset(enabled):
                raise LinsightAgentConfigurationError(msg="Selected skill is unavailable")
            digests[member["id"]] = {
                name: await asyncio.to_thread(skill_digest, self.store or SkillStore(), tenant_id, name)
                for name in (member["skill_names"] if include_skill_digests else [])
            }
        snapshot.update(event_protocol=2, recovery_protocol=1, main_model_id=str(default), skill_digests=digests)
        return snapshot

    async def revalidate(self, snapshot: dict, user, tenant_id: int):
        config = AgentConfig.model_validate({key: snapshot[key] for key in ("schema_version", "subagents")})
        current = await self.resolve(config, user, tenant_id, snapshot["main_model_id"])
        if current["skill_digests"] != snapshot["skill_digests"]:
            raise LinsightAgentConfigurationError(msg="Selected skills changed; start a new task")

    async def owned_task(self, task_id: str, user):
        from bisheng.common.errcode.http_error import UnAuthorizedError

        task = await self.repository.task(task_id)
        from bisheng.permission.domain.services.permission_service import PermissionService

        if task is None or not PermissionService.check_private_session_owner(
            user.user_id, task.user_id, task.tenant_id
        ):
            raise UnAuthorizedError()
        return task
