"""Build collaborators using the existing resource resolvers and safety stack."""

from deepagents.backends.filesystem import FilesystemBackend
from deepagents.middleware.skills import SkillsMiddleware
from langchain.agents.middleware import AgentMiddleware
from loguru import logger

from bisheng.common.dependencies.user_deps import UserPayload
from bisheng.common.errcode.linsight import LinsightAgentConfigurationError
from bisheng.linsight.domain.schemas.agent_config import AgentConfig
from bisheng.linsight.domain.services.agent_config_service import AgentConfigService
from bisheng.linsight.domain.services.skill_provisioning import materialize_session_skills
from bisheng.linsight.domain.services.subagent_context import DelegationMiddleware, SubagentIdentityMiddleware


class MemberResourceGuard(AgentMiddleware):
    def __init__(self, member, user, tenant_id):
        self.config = AgentConfig(subagents=[member])
        self.user = user
        self.tenant_id = tenant_id
        self.service = AgentConfigService()

    async def validate(self):
        await self.service.validate_runtime_member(self.config.subagents[0], self.user)

    async def awrap_model_call(self, request, handler):
        await self.validate()
        return await handler(request)

    async def awrap_tool_call(self, request, handler):
        await self.validate()
        return await handler(request)


def bind_cloud_workspace(tools, backend, *, require_e2b: bool = True):
    """Attach the workspace backend to the E2B executor found in ``tools``.

    ``require_e2b=True`` is the collaborator contract: member code must never
    fall back to host execution, so a non-E2B executor is rejected. The main
    graph passes ``require_e2b=False`` — only E2B executors have a copy-out
    path that persists files through this backend, while a local executor
    keeps its previous behavior instead of failing the whole task.
    """
    from bisheng_langchain.gpts.tools.code_interpreter.e2b_executor import E2bCodeExecutor

    for tool in tools:
        if tool.name == "bisheng_code_interpreter":
            code_tool = getattr(tool, "tool_instance", tool)
            executor = getattr(code_tool, "executor", None)
            if not isinstance(executor, E2bCodeExecutor):
                if require_e2b:
                    raise LinsightAgentConfigurationError(msg="Harness code execution requires an E2B sandbox")
                logger.warning(
                    "bind_cloud_workspace: code interpreter is not an E2B executor ({}); "
                    "workspace copy-out binding skipped",
                    type(executor).__name__,
                )
                continue
            executor.workspace_backend = backend


async def build_configured_subagents(session_model, backend, file_dir, middleware_factory, resource_sink=None):
    from bisheng.linsight.domain.services.agent_factory import (
        SUBAGENT_ARTIFACT_REPORT_CONTRACT,
        _resolve_model,
        _subagent_tools,
    )
    from bisheng.linsight.domain.services.workbench_impl import LinsightWorkbenchImpl

    snapshot = session_model.agent_config
    user = UserPayload(user_id=session_model.user_id, user_name="", user_role=[], tenant_id=session_model.tenant_id)
    await AgentConfigService().revalidate(snapshot, user, session_model.tenant_id)
    specs = []
    members = {}
    for member in snapshot["subagents"]:
        # Every collaborator uses its own ``member-<id>`` slot. Member #0 used to
        # take over the built-in researcher's ``general-purpose`` name, which
        # silently removed the researcher (and its inherited research tools)
        # whenever the first member had none (live test 2026-10-16: a tool-less
        # member made "资料研究员" delegation impossible). The researcher stays
        # available; collaborators are additive.
        internal_name = f"member-{member['id']}"
        members[internal_name] = member
        skill_middleware = []
        if member["skill_names"]:
            skill_dir = f"skills/agents/{member['id']}"
            copied = await materialize_session_skills(
                backend,
                session_model.tenant_id,
                member["skill_names"],
                workspace_dir=skill_dir,
            )
            if set(copied) != set(member["skill_names"]) or not file_dir:
                raise LinsightAgentConfigurationError(msg="Collaborator skills could not be loaded")
            skill_middleware.append(
                SkillsMiddleware(
                    backend=FilesystemBackend(root_dir=file_dir, virtual_mode=True),
                    sources=[(f"/{skill_dir}/", "Skills")],
                )
            )
        model = await _resolve_model(session_model, member["model_id"])
        ids = [*member["tool_refs"], *member["connector_refs"]]
        selected_session = session_model.model_copy(update={"tools": [{"children": [{"id": i} for i in ids]}]})
        tools = (
            await LinsightWorkbenchImpl.init_linsight_config_tools(
                selected_session,
                llm=model,
                need_upload=True,
                file_dir=file_dir,
            )
            if ids
            else []
        )
        if resource_sink is not None:
            resource_sink.extend(tools)
        bind_cloud_workspace(tools, backend)
        if set(ids) != {getattr(tool, "tool_id", None) for tool in tools}:
            raise LinsightAgentConfigurationError(msg="Selected tools could not all be initialized")
        allowed = _subagent_tools(tools)
        if len(allowed) != len(tools):
            raise LinsightAgentConfigurationError(msg="Selected tool is not supported in collaborators")
        names = [tool.name for tool in allowed]
        if len(names) != len(set(names)):
            raise LinsightAgentConfigurationError(msg="Collaborator tools have conflicting names")
        middleware = [
            SubagentIdentityMiddleware(),
            MemberResourceGuard(member, user, session_model.tenant_id),
            *middleware_factory(allowed),
            *skill_middleware,
        ]
        specs.append(
            {
                "name": internal_name,
                "description": f"{member['name']}: {member['description']}",
                # T1: every collaborator closes its final message with a
                # `产物:` line (single artifact channel — no auto injection).
                "system_prompt": (member["system_prompt"] or member["description"])
                + SUBAGENT_ARTIFACT_REPORT_CONTRACT,
                "model": model,
                "tools": allowed,
                "middleware": middleware,
            }
        )
    return specs, DelegationMiddleware(members)
