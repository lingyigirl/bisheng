"""F078 managed-agent constants: audit actions and app-type sentinel.

The audit names are flat (``managed_agent.<verb>``) so the namespace prefix map
stays one-to-one. Management actions are UI-visible; ``managed_agent.invoke`` is
DB-only forensics, matching ``open_api.call`` / ``schedule.run``.
"""

from __future__ import annotations

from enum import StrEnum


class ManagedAgentAuditAction(StrEnum):
    CREATE = "managed_agent.create"
    UPDATE = "managed_agent.update"
    DELETE = "managed_agent.delete"
    ENABLE = "managed_agent.enable"
    DISABLE = "managed_agent.disable"
    HEALTH_CHECK = "managed_agent.health_check"
    INVOKE = "managed_agent.invoke"


#: Actions surfaced on the audit page (invoke deliberately excluded).
UI_VISIBLE_ACTIONS: tuple[str, ...] = (
    ManagedAgentAuditAction.CREATE.value,
    ManagedAgentAuditAction.UPDATE.value,
    ManagedAgentAuditAction.DELETE.value,
    ManagedAgentAuditAction.ENABLE.value,
    ManagedAgentAuditAction.DISABLE.value,
    ManagedAgentAuditAction.HEALTH_CHECK.value,
)

#: Sentinel stored in ``scheduled_task.app_type`` for managed agents. Kept out
#: of ``FlowType`` on purpose: the Flow table only holds native apps.
MANAGED_AGENT_APP_TYPE = 100
