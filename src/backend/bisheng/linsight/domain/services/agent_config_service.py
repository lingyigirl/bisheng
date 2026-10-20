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


def skill_digest(store: SkillStore, tenant_id: int, name: str, version=None) -> str:
    # Preserve F074 historical digests: object-storage hashes use another format.
    try:
        files = store.load_bundle(tenant_id, name, version)
    except (FileNotFoundError, ValueError) as exc:
        raise LinsightAgentConfigurationError(msg="Skill bundle is missing or invalid") from exc
    digest = hashlib.sha256()
    for path in sorted(files):
        digest.update(path.encode())
        digest.update(b"\0")
        digest.update(files[path])
    return digest.hexdigest()


class AgentConfigService:
    def __init__(self, repository=None, store=None):
        self.repository = repository or AgentConfigRepository()
        self.store = store

    async def validate_runtime_member(self, member, user):
        """Fresh, minimal reads per call; permissions are never cached across steps."""
        ids = [*member.tool_refs, *member.connector_refs]
        model, rows, enabled = await asyncio.gather(
            LLMService.get_model_for_call(int(member.model_id)),
            self.repository.runtime_tools(ids),
            self.repository.enabled_skill_names(member.skill_names),
        )
        if not model.online or model.model_type != "llm":
            raise LinsightAgentConfigurationError(msg="Selected model is unavailable for chat")
        if set(ids) != {row.id for row in rows if not row.is_delete and not row.group_deleted}:
            raise LinsightAgentConfigurationError(msg="Selected tool is unavailable")
        groups = set()
        for row in rows:
            if row.id in member.connector_refs and row.is_preset == ToolPresetType.PRESET.value:
                raise LinsightAgentConfigurationError(msg="A connector must reference an API or MCP action")
            if row.is_preset != ToolPresetType.PRESET.value:
                groups.add(str(row.type))
        if groups:
            permissions = await ToolPermissionService.get_tool_permission_map_async(user, sorted(groups), ["use_tool"])
            if any("use_tool" not in permissions.get(group, set()) for group in groups):
                raise LinsightAgentConfigurationError(msg="Selected tool is not authorized")
        if not set(member.skill_names).issubset(enabled):
            raise LinsightAgentConfigurationError(msg="Selected skill is unavailable")

    async def resolve(
        self,
        config: AgentConfig | None,
        user,
        tenant_id: int,
        main_model: str | None,
        *,
        include_skill_digests: bool = True,
    ):
        # Harness is the execution protocol for every NEW task, independent of
        # optional collaborators. Historical rows are never resolved implicitly.
        config = config or AgentConfig()
        default = main_model or (await LLMService.get_workbench_llm(tenant_id=tenant_id)).linsight_default_model_id
        if not default:
            raise LinsightAgentConfigurationError(msg="No task model is configured")
        enabled = (
            {skill.name: skill for skill in await self.repository.enabled_skills()}
            if any(member.skill_names for member in config.subagents)
            else {}
        )
        snapshot = config.model_dump()
        digests = {}
        computed = {}
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
            for name in member["skill_names"] if include_skill_digests else []:
                if name not in computed:
                    computed[name] = await asyncio.to_thread(
                        skill_digest,
                        self.store or SkillStore(),
                        tenant_id,
                        name,
                        getattr(enabled[name], "content_hash", None),
                    )
            digests[member["id"]] = {
                name: computed[name] for name in (member["skill_names"] if include_skill_digests else [])
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
