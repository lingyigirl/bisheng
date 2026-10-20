"""Copy a run's selected skill bundles into the session workspace (F035, Fork X).

deepagents' ``SkillsMiddleware`` discovers a skill from the *directory* entries an
``ls`` returns (``is_dir=True``) and the model reads the body via the workspace
``read_file`` tool. Two facts make a naive wiring impossible:

  * the session ``WorkspaceBackend`` lists MinIO recursively and returns only
    *file* entries (``is_dir=False``) — deepagents' native ``skills=`` param
    pointed at it would discover zero skills;
  * skill bundles live in object storage with a separate local cache the workspace ``read_file``
    cannot reach.

So at task startup we copy the bundles this run is allowed to use into the
workspace ``/skills/`` subtree. ``WorkspaceBackend.aupload_files`` write-throughs
to both MinIO and the local cache, after which a plain ``SkillsMiddleware`` backed
by a ``FilesystemBackend`` over that cache can enumerate them (real on-disk dirs,
``is_dir``-aware) and the model reads the very same ``/skills/<name>/SKILL.md``
paths back through the workspace backend.

The copy IS the whitelist gate (Fork X): only ``enabled (DB governance) ∩
selected (this run)`` bundles are materialized, so the model physically cannot
see a skill it was not granted — no per-run config key, no runtime filter. This
replaces the dormant ``TenantSkillsMiddleware`` runtime whitelist.
"""

from __future__ import annotations

import asyncio

from loguru import logger

from bisheng.common.errcode.linsight import LinsightAgentConfigurationError
from bisheng.linsight.domain.models.linsight_skill import LinsightSkillDao
from bisheng.linsight.domain.services.skill_store import SkillStore

WORKSPACE_SKILLS_DIR = "skills"
"""Workspace subtree the copied bundles live under (``/skills/<name>/...``)."""


def _collect_bundle_pairs(store, tenant_id, name, workspace_dir=WORKSPACE_SKILLS_DIR, version=None):
    files = store.load_bundle(tenant_id, name, version)
    return [(f"/{workspace_dir}/{name}/{path}", data) for path, data in sorted(files.items())]


async def materialize_session_skills(
    backend,
    tenant_id: int,
    selected: list[str] | None,
    store: SkillStore | None = None,
    workspace_dir: str = WORKSPACE_SKILLS_DIR,
) -> list[str]:
    """Copy allowed skill bundles into the workspace ``/skills/`` subtree.

    Args:
        backend: the session ``WorkspaceBackend`` (write-throughs to MinIO+cache).
        tenant_id: owning tenant; scopes the on-disk bundle source path.
        selected: skill names picked for this run. Both ``None`` (field absent —
            a legacy row, or any client/caller that never sent it) and ``[]`` (the
            UI explicitly cleared the picker) mean "no skills this run": copy
            nothing. Only an explicit non-empty list opts in, and each name is
            still intersected with the tenant's governance-enabled set.
        store: versioned skill store (injectable for tests).

    Returns:
        The skill names actually materialized. Empty when nothing matched — the
        caller then skips attaching the skills middleware entirely.
    """
    # None ≡ [] ≡ "no skills for this run" — copy nothing. Treating a missing
    # field as "copy every enabled skill" was a footgun: any request that omitted
    # skills (a stale/cached client, a non-UI caller, a legacy row) silently
    # loaded EVERY enabled skill, defeating the picker. Skills are strictly opt-in
    # via an explicit name list.
    if not selected:
        return []

    store = store or SkillStore()
    # Governance gate, scoped to the current tenant (LinsightSkillDao.list_enabled
    # uses strict_tenant_filter); the worker has already restored tenant context.
    rows = {skill.name: skill for skill in await LinsightSkillDao.list_enabled()}
    wanted = sorted(set(selected))
    if not set(wanted).issubset(rows):
        raise LinsightAgentConfigurationError(msg="Selected Skill is unavailable")
    # The agent is only assembled after all uploads succeed; keep one bundle in memory.
    for name in wanted:
        try:
            pairs = await asyncio.to_thread(
                _collect_bundle_pairs, store, tenant_id, name, workspace_dir, getattr(rows[name], "content_hash", None)
            )
        except (FileNotFoundError, ValueError) as exc:
            logger.warning("skill unavailable: tenant={} name={} category={}", tenant_id, name, type(exc).__name__)
            raise LinsightAgentConfigurationError(msg="Selected Skill bundle is missing or invalid") from exc
        responses = await backend.aupload_files(pairs)
        if any(getattr(response, "error", None) for response in responses):
            raise LinsightAgentConfigurationError(msg="Selected Skill could not be copied to the workspace")
    logger.info(
        "skill provisioning complete: tenant={} selected={} enabled={} -> materialized {}",
        tenant_id,
        selected,
        sorted(rows),
        wanted,
    )
    return wanted
