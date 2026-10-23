"""F076 increment: management-side helpers for the per-key agent allowlist.

Two responsibilities:

1. ``normalize_refs`` / ``assert_assignable`` -- validate the ``allowed_apps``
   payload on issue/PATCH. Write-time validation only requires the resource to
   be **visible** to the service account's owner; the invocation tier is still
   enforced at call time (a viewer-only workflow/assistant can be scoped for
   read endpoints, but invoking it will still return 11809).
2. ``list_assignable_agents`` -- the console picker's data source: the account's
   granted assistants / workflows / managed agents, with resolved names and an
   advisory ``invocable`` flag.

Nothing here grants a resource: the allowlist is a restriction layered on top
of the account's ReBAC grants (INV-27).
"""

from __future__ import annotations

from loguru import logger
from sqlmodel import select

from bisheng.common.dependencies.user_deps import UserPayload
from bisheng.common.errcode.open_api import OpenApiAgentNotAssignableError
from bisheng.common.services.config_service import settings
from bisheng.core.database import get_async_db_session
from bisheng.database.models.assistant import AssistantDao
from bisheng.database.models.flow import Flow
from bisheng.open_api.domain.agent_scope import AGENT_TYPES, normalize_agent_id
from bisheng.open_api.domain.services.resource_grant_service import list_grants

#: Defensive cap: the allowlist lives in a JSON column and is read on every
#: request through the credential cache. 200 entries is far above any real
#: integration and keeps the payload bounded.
MAX_ALLOWED_APPS = 200


def normalize_refs(refs) -> list[dict] | None:
    """Normalize/validate ``allowed_apps`` entries.

    ``None`` stays ``None`` (unrestricted). Duplicates collapse; an invalid
    type/id is a 400/11822 rather than a silently dropped entry.
    """
    if refs is None:
        return None
    if len(refs) > MAX_ALLOWED_APPS:
        raise OpenApiAgentNotAssignableError(exception=f"at most {MAX_ALLOWED_APPS} entries are allowed")
    normalized: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for ref in refs:
        if isinstance(ref, dict):
            agent_type = str(ref.get("type") or "")
            agent_id_raw = ref.get("id")
        else:
            agent_type = str(getattr(ref, "type", "") or "")
            agent_id_raw = getattr(ref, "id", None)
        agent_id = normalize_agent_id(agent_type, agent_id_raw)
        if agent_type not in AGENT_TYPES or agent_id is None:
            raise OpenApiAgentNotAssignableError(agent_type=agent_type, agent_id=str(agent_id_raw))
        key = (agent_type, agent_id)
        if key in seen:
            continue
        seen.add(key)
        normalized.append({"type": agent_type, "id": agent_id})
    return normalized


def existing_ref_keys(refs) -> set[tuple[str, str]]:
    """Current allowlist as a set of ``(type, id)`` for PATCH diffing."""
    if not refs:
        return set()
    return {
        (str(item.get("type")), str(item.get("id")))
        for item in refs
        if isinstance(item, dict) and item.get("type") and item.get("id")
    }


async def assert_assignable(
    owner: UserPayload,
    refs: list[dict] | None,
    *,
    existing: set[tuple[str, str]] | None = None,
) -> None:
    """Reject refs the service account cannot even see (400/11822).

    ``existing`` lets PATCH skip entries that have not changed: after a grant is
    revoked, a rename must not become permanently impossible.
    """
    if not refs:
        return
    from bisheng.permission.domain.services.application_permission_service import ApplicationPermissionService
    from bisheng.permission.domain.services.permission_service import PermissionService

    already = existing or set()
    for ref in refs:
        agent_type = ref["type"]
        agent_id = ref["id"]
        if (agent_type, agent_id) in already:
            continue
        if agent_type in ("assistant", "workflow"):
            visible = await ApplicationPermissionService.has_any_permission_async(
                owner, agent_type, agent_id, ["view_app", "use_app"]
            )
        else:
            visible = await PermissionService.check(
                owner.user_id, "viewer", "managed_agent", agent_id, login_user=owner
            )
        if not visible:
            raise OpenApiAgentNotAssignableError(agent_type=agent_type, agent_id=agent_id)


async def _load_names(rows: list[dict]) -> dict[tuple[str, str], str]:
    """Resolve display names in batches; unknown ids fall back to the raw id."""
    names: dict[tuple[str, str], str] = {}
    # Rows come from resource_grant_service.list_grants(): resource_type /
    # resource_id (not the {type, id} shape used by the allowlist itself).
    assistant_ids = [str(row["resource_id"]) for row in rows if row["resource_type"] == "assistant"]
    workflow_ids = [str(row["resource_id"]) for row in rows if row["resource_type"] == "workflow"]
    managed_ids = [str(row["resource_id"]) for row in rows if row["resource_type"] == "managed_agent"]

    if assistant_ids:
        try:
            for one in await AssistantDao.aget_assistants_by_ids(assistant_ids):
                names[("assistant", str(one.id))] = one.name
        except Exception:  # display-only lookup; the picker can show raw ids
            logger.opt(exception=True).warning("open_api: assistant name lookup failed")
    if workflow_ids:
        try:
            async with get_async_db_session() as session:
                result = await session.exec(select(Flow).where(Flow.id.in_(workflow_ids)))
                for one in result.all():
                    names[("workflow", str(one.id))] = one.name
        except Exception:  # display-only lookup
            logger.opt(exception=True).warning("open_api: workflow name lookup failed")
    if managed_ids and settings.managed_agent.enabled:
        try:
            from bisheng.managed_agent.domain.models.dao import ManagedAgentDao

            for one_id in managed_ids:
                row = await ManagedAgentDao.aget(int(one_id))
                if row is not None:
                    names[("managed_agent", str(row.id))] = row.name
        except Exception:  # display-only lookup
            logger.opt(exception=True).warning("open_api: managed-agent name lookup failed")
    return names


async def list_assignable_agents(owner: UserPayload) -> list[dict]:
    """Return app-type grants of the service-account owner, with names.

    The owner's active tenant is the account's tenant (the management request
    runs in that tenant), so the regular tenant filter is correct here.
    """
    from bisheng.permission.domain.services.application_permission_service import ApplicationPermissionService
    from bisheng.permission.domain.services.permission_service import PermissionService

    rows = [row for row in await list_grants(owner.user_id) if row["resource_type"] in AGENT_TYPES]
    if not rows:
        return []
    names = await _load_names(rows)

    out: list[dict] = []
    for row in rows:
        agent_type = row["resource_type"]
        agent_id = str(row["resource_id"])
        if agent_type == "assistant":
            # 2026-10-22:调用口径与工作流对齐(use_app,editor 起);
            # view_app 仅够详情/列表读取,不再算 invocable。
            invocable = await ApplicationPermissionService.has_any_permission_async(
                owner, "assistant", agent_id, ["use_app"]
            )
        elif agent_type == "workflow":
            invocable = await ApplicationPermissionService.has_any_permission_async(
                owner, "workflow", agent_id, ["use_app"]
            )
        else:
            if not settings.managed_agent.enabled:
                continue
            invocable = await PermissionService.check(
                owner.user_id, "editor", "managed_agent", agent_id, login_user=owner
            )
        out.append(
            {
                "type": agent_type,
                "id": agent_id,
                "name": names.get((agent_type, agent_id), agent_id),
                "level": row.get("level"),
                "invocable": bool(invocable),
            }
        )
    out.sort(key=lambda item: (item["type"], item["name"]))
    return out
