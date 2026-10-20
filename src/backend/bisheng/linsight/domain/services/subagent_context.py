"""Explicit delegation identities propagated through asynchronous child graphs."""

import asyncio
from contextvars import ContextVar

from langchain.agents.middleware import AgentMiddleware
from langgraph.config import get_stream_writer

delegation_context: ContextVar[dict | None] = ContextVar("linsight_delegation", default=None)


class DelegationMiddleware(AgentMiddleware):
    def __init__(self, members: dict[str, dict]):
        self.members = members
        self.slots = asyncio.Semaphore(2)

    async def awrap_tool_call(self, request, handler):
        if request.tool_call["name"] != "task":
            return await handler(request)
        args = request.tool_call.get("args", {})
        member = self.members.get(args.get("subagent_type"))
        if member is None:
            return await handler(request)
        identity = {
            "agent_id": member["id"],
            "agent_name": member["name"],
            "invocation_id": request.tool_call["id"],
            "parent_call_id": request.tool_call["id"],
            "delegate_goal": args.get("description", ""),
        }
        async with self.slots:
            token = delegation_context.set(identity)
            try:
                get_stream_writer()({"type": "subagent_identity", **identity})
                return await handler(request)
            except Exception:
                get_stream_writer()({"type": "subagent_failure", **identity, "outcome": "error"})
                raise
            finally:
                delegation_context.reset(token)


class SubagentIdentityMiddleware(AgentMiddleware):
    def _emit_identity(self):
        identity = delegation_context.get()
        if identity is None:
            raise RuntimeError("Missing delegation identity")
        get_stream_writer()({"type": "subagent_identity", **identity})

    async def abefore_agent(self, state, runtime):
        self._emit_identity()
        return None

    async def awrap_model_call(self, request, handler):
        # Checkpoint resume can skip before_agent; publish the namespace again.
        self._emit_identity()
        return await handler(request)

    async def awrap_tool_call(self, request, handler):
        self._emit_identity()
        return await handler(request)
