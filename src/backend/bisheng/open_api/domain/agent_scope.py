"""F076 increment: per-key agent allowlist (spec AC-42~AC-46, INV-27).

The allowlist is a **restriction only**: it never grants a resource, it just
narrows which assistants / workflows / managed agents a particular API key may
address. The three layers that must all pass are:

1. the key's functional scope (``assistant:invoke`` etc.);
2. the target object being in this key's allowlist (``None`` = unrestricted);
3. the service account owner's ReBAC grant on that object.

``None`` and ``[]`` are deliberately different: ``None`` means "no restriction"
(legacy keys), while ``[]`` means "this key may not call any agent".
"""

from __future__ import annotations

import uuid
from typing import Any

from bisheng.common.errcode.open_api import OpenApiNotFoundError
from bisheng.open_api.domain.context import OpenApiPrincipal, get_open_api_principal

AGENT_TYPES = ("assistant", "workflow", "managed_agent")


def normalize_agent_id(agent_type: str, raw: Any) -> str | None:
    """Normalize an agent reference id to the form stored in the allowlist.

    Assistants and workflows are UUID hex strings (32 lowercase chars); managed
    agents use their integer registry id as a string. Invalid input returns
    ``None`` so callers can treat it as "not in the allowlist" instead of
    raising (the value often comes from a path parameter).
    """
    if raw is None:
        return None
    try:
        if agent_type in ("assistant", "workflow"):
            return uuid.UUID(str(raw)).hex
        if agent_type == "managed_agent":
            if isinstance(raw, bool):
                return None
            value = int(str(raw))
            return str(value) if value > 0 else None
    except (TypeError, ValueError, AttributeError):
        return None
    return None


def parse_allowed_apps(raw: Any) -> frozenset[tuple[str, str]] | None:
    """Convert the JSON column into the principal's frozenset.

    ``None`` stays ``None`` (unrestricted). Any non-null malformed value becomes
    an empty set, i.e. fail-closed (deny every agent), never "accidentally
    unrestricted".
    """
    if raw is None:
        return None
    if not isinstance(raw, list):
        return frozenset()
    parsed: set[tuple[str, str]] = set()
    for item in raw:
        if not isinstance(item, dict):
            continue
        agent_type = str(item.get("type") or "")
        if agent_type not in AGENT_TYPES:
            continue
        agent_id = normalize_agent_id(agent_type, item.get("id"))
        if agent_id is not None:
            parsed.add((agent_type, agent_id))
    return frozenset(parsed)


def allowed_apps_to_json(principal: OpenApiPrincipal) -> list[dict[str, str]] | None:
    """Serialize the principal's allowlist back to the public JSON shape."""
    if principal.allowed_apps is None:
        return None
    return [{"type": agent_type, "id": agent_id} for agent_type, agent_id in sorted(principal.allowed_apps)]


def agent_allowed(principal: OpenApiPrincipal | None, agent_type: str, agent_id: Any) -> bool:
    """Whether this principal may address the given agent object."""
    if principal is None or principal.allowed_apps is None:
        return True
    normalized = normalize_agent_id(agent_type, agent_id)
    return normalized is not None and (agent_type, normalized) in principal.allowed_apps


def assert_open_api_agent_allowed(agent_type: str, agent_id: Any) -> None:
    """Fail-closed 404/11809 when a key's allowlist excludes the target object.

    Non-key calls (no principal) keep the legacy behaviour and pass through.
    The caller must only invoke this for key-authenticated requests; the helper
    itself checks the principal so total-switch-off deployments are unaffected.
    """
    principal = get_open_api_principal()
    if principal is None:
        return
    if not agent_allowed(principal, agent_type, agent_id):
        raise OpenApiNotFoundError()


def assert_open_api_agent_allowed_any(app_id: Any) -> None:
    """Allowlist check when the object type is not known (session/message paths).

    Only assistants and workflows are considered: those are the two object types
    whose ids can appear as ``flow_id`` on chat sessions.
    """
    principal = get_open_api_principal()
    if principal is None or principal.allowed_apps is None:
        return
    for agent_type in ("assistant", "workflow"):
        if agent_allowed(principal, agent_type, app_id):
            return
    raise OpenApiNotFoundError()


def assistant_ids_in_allowlist() -> set[str] | None:
    """Assistant ids from the current key's allowlist, or ``None`` if unrestricted.

    ``[]`` (explicit deny-all) returns an empty set, which the list endpoint must
    treat as "return nothing" -- not as "no filter".
    """
    principal = get_open_api_principal()
    if principal is None or principal.allowed_apps is None:
        return None
    return {agent_id for agent_type, agent_id in principal.allowed_apps if agent_type == "assistant"}
