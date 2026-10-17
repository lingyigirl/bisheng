"""Integration boundary owned by WBS 1.3; storage and curation belong to 8.2/8.3."""

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class MemoryScope:
    tenant_id: int
    user_id: int
    app_id: str = "linsight"


class MemoryAdapter(Protocol):
    available: bool

    async def load_context(self, scope: MemoryScope, goal: str) -> str: ...

    async def submit_candidates(self, scope: MemoryScope, task_id: str, candidates: list[str]) -> dict: ...


class UnavailableMemoryAdapter:
    available = False

    async def load_context(self, scope: MemoryScope, goal: str) -> str:
        return ""

    async def submit_candidates(self, scope: MemoryScope, task_id: str, candidates: list[str]) -> dict:
        return {"status": "unavailable", "saved": False}


async def memory_bindings(adapter: MemoryAdapter, session):
    from langchain_core.tools import tool

    if not adapter.available:
        return "", []
    scope = MemoryScope(tenant_id=session.tenant_id, user_id=session.user_id)
    context = await adapter.load_context(scope, session.question)

    @tool
    async def propose_memory(experience: str) -> dict:
        """Submit a concise experience candidate to the configured memory service."""
        if not experience.strip() or len(experience) > 4000:
            return {"status": "invalid", "saved": False}
        return await adapter.submit_candidates(scope, session.id, [experience])

    return context[:8000], [propose_memory]
