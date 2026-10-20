"""Saved-collaborator library service (F087, 2026-10-16).

Two validation layers, deliberately:

1. SAVE runs the full ``AgentConfigService.resolve`` gate — exactly what a task
   submission with this member would face (model online/llm, tools exist +
   authorized, connector shape, skills ⊆ tenant-enabled). A template therefore
   can never encode resources its owner could not select manually.
2. LIST re-checks availability only (resources get deleted later) and reports
   per-member ``issues`` for the UI to badge. The submit-time validation stays
   the final gate — nothing here weakens it.

Listing is batched: one tools query, one enabled-skills query and one model
lookup per unique model id for the WHOLE library, not per member.
"""

from __future__ import annotations

import logging

from bisheng.common.errcode.linsight import LinsightAgentConfigurationError
from bisheng.core.context.tenant import DEFAULT_TENANT_ID, get_current_tenant_id
from bisheng.linsight.domain.models.linsight_agent_member import LinsightAgentMember, LinsightAgentMemberDao
from bisheng.linsight.domain.repositories.agent_config_repository import AgentConfigRepository
from bisheng.linsight.domain.schemas.agent_config import AgentConfig, SubagentConfig
from bisheng.linsight.domain.schemas.member_schema import MemberSaveRequest
from bisheng.linsight.domain.services.agent_config_service import AgentConfigService
from bisheng.llm.domain.services import LLMService

logger = logging.getLogger(__name__)


class MemberService:
    def __init__(self, repository: AgentConfigRepository | None = None, dao=None) -> None:
        self.repository = repository or AgentConfigRepository()
        self.dao = dao or LinsightAgentMemberDao

    # ------------------------------------------------------------------ read

    async def list_members(self, user) -> list[dict]:
        rows = await self.dao.list_by_user(user.user_id)
        if not rows:
            return []
        alive_tool_ids = await self._alive_tool_ids(rows)
        enabled_skills = await self._enabled_skill_names(rows)
        model_ok = {mid: await self._model_available(mid) for mid in {row.model_id for row in rows if row.model_id}}

        out: list[dict] = []
        for row in rows:
            ids = [*(row.tool_refs or []), *(row.connector_refs or [])]
            missing_tools = [i for i in ids if i not in alive_tool_ids]
            missing_skills = [n for n in (row.skill_names or []) if n not in enabled_skills]
            issues: dict = {}
            if missing_tools:
                issues["tools"] = missing_tools
            if missing_skills:
                issues["skills"] = missing_skills
            if row.model_id and not model_ok.get(row.model_id, True):
                issues["model"] = True
            out.append(self._to_dict(row, issues))
        return out

    async def _alive_tool_ids(self, rows) -> set[int]:
        ids = sorted({i for row in rows for i in [*(row.tool_refs or []), *(row.connector_refs or [])]})
        if not ids:
            return set()
        try:
            records = await self.repository.tools(ids)
            return {record.id for record in records if not record.is_delete}
        except Exception:  # a failed check must not fake "everything is missing"
            logger.exception("member library: tool availability check failed")
            return set(ids)

    async def _enabled_skill_names(self, rows) -> set[str]:
        names = {name for row in rows for name in (row.skill_names or [])}
        if not names:
            return set()
        try:
            return {skill.name for skill in await self.repository.enabled_skills()}
        except Exception:  # same guard as above
            logger.exception("member library: skill availability check failed")
            return names

    @staticmethod
    async def _model_available(model_id: str) -> bool:
        try:
            model = await LLMService.get_model_for_call(int(model_id))
            return bool(model and model.online and model.model_type == "llm")
        except Exception:
            return False

    # ----------------------------------------------------------------- write

    async def save_member(self, user, payload: MemberSaveRequest) -> dict | None:
        tenant_id = get_current_tenant_id() or DEFAULT_TENANT_ID
        target = None
        if payload.id is not None:
            # Update path: the row must be owned by this user; a rename must
            # not collide with another of the user's rows (the DB unique key
            # would otherwise surface as a raw integrity error).
            target = await self.dao.get_by_id(payload.id, user.user_id)
            if target is None:
                return None
            clash = await self.dao.get_by_name(user.user_id, payload.name)
            if clash is not None and clash.id != target.id:
                raise LinsightAgentConfigurationError(msg="A saved member with this name already exists")
        # Same gate a task submission with this member would face; raises with a
        # precise reason (unavailable tool / unauthorized connector / unknown
        # skill / offline model) instead of storing a broken template.
        await AgentConfigService().resolve(
            AgentConfig(
                subagents=[
                    SubagentConfig(
                        id="saved",
                        name=payload.name,
                        description=payload.description,
                        system_prompt=payload.system_prompt,
                        model_id=payload.model_id,
                        skill_names=payload.skill_names,
                        tool_refs=payload.tool_refs,
                        connector_refs=payload.connector_refs,
                    )
                ]
            ),
            user,
            tenant_id,
            None,
            include_skill_digests=False,
        )
        if target is not None:
            target.name = payload.name
            target.description = payload.description
            target.system_prompt = payload.system_prompt
            target.model_id = payload.model_id
            target.skill_names = list(payload.skill_names)
            target.tool_refs = list(payload.tool_refs)
            target.connector_refs = list(payload.connector_refs)
            row = await self.dao.update(target)
        else:
            row = await self.dao.upsert(
                LinsightAgentMember(
                    tenant_id=tenant_id,
                    user_id=user.user_id,
                    name=payload.name,
                    description=payload.description,
                    system_prompt=payload.system_prompt,
                    model_id=payload.model_id,
                    skill_names=list(payload.skill_names),
                    tool_refs=list(payload.tool_refs),
                    connector_refs=list(payload.connector_refs),
                )
            )
        return self._to_dict(row, {})

    async def delete_member(self, user, member_id: int) -> bool:
        return await self.dao.delete(member_id, user.user_id)

    async def set_default(self, user, member_id: int, is_default: bool) -> bool:
        return await self.dao.set_default(member_id, user.user_id, is_default)

    # ------------------------------------------------------------- serialize

    @staticmethod
    def _to_dict(row: LinsightAgentMember, issues: dict) -> dict:
        return {
            "id": row.id,
            "name": row.name,
            "description": row.description,
            "system_prompt": row.system_prompt or "",
            "model_id": row.model_id,
            "skill_names": list(row.skill_names or []),
            "tool_refs": list(row.tool_refs or []),
            "connector_refs": list(row.connector_refs or []),
            "is_default": bool(row.is_default),
            "issues": issues,
            "create_time": row.create_time.isoformat() if row.create_time else None,
            "update_time": row.update_time.isoformat() if row.update_time else None,
        }
