"""Template-change audit trail.

Reuses the platform's structured v2 audit path (``AuditLogDao.ainsert_v2``)
rather than a dedicated table: the platform already has an audit trail, an
audit page, and a tenant-visibility model, and a second one would drift from it.

Two things must stay in lockstep for a row to be reachable from the audit page,
both handled here / in the module constants:

1. ``_UI_VISIBLE_V2_ACTIONS`` in ``database/models/audit_log.py``
2. the mirror list in the platform's ``controllers/API/log.ts`` (+ its i18n keys)

Note the v2 path deliberately does NOT set ``system_id``. Visibility comes from
the action whitelist instead, which is why the enum lives in
``domain/constants.py`` -- a typo'd action string writes a row that no operator
can ever see, with no error anywhere.
"""

from __future__ import annotations

from typing import Any

from loguru import logger

from bisheng.core.context.tenant import get_current_tenant_id
from bisheng.database.models.audit_log import AuditLogDao
from bisheng.doc_extract.domain.constants import DocExtractAuditAction

ROOT_TENANT_ID = 1


async def write_template_audit(
    *,
    action: DocExtractAuditAction,
    operator_id: int,
    template_id: int | None,
    template_name: str,
    doc_type: str,
    tenant_id: int | None = None,
    extra: dict[str, Any] | None = None,
) -> None:
    """Record one template change.

    Best-effort: an audit write must never fail the operation that triggered it
    -- losing a log line is strictly better than losing the admin's edit. The
    failure is logged loudly so it does not pass silently.

    ``tenant_id`` is the resource-owning tenant; the caller's effective scope
    goes in ``operator_tenant_id`` (an admin-scoped super user operating on a
    child tenant must still be attributable to both).
    """
    try:
        await AuditLogDao.ainsert_v2(
            tenant_id=tenant_id or get_current_tenant_id() or ROOT_TENANT_ID,
            operator_id=operator_id,
            operator_tenant_id=get_current_tenant_id() or ROOT_TENANT_ID,
            action=action.value,
            target_type='doc_extract_template',
            target_id=str(template_id or ''),
            object_name=template_name,
            metadata={
                'template_name': template_name,
                'doc_type': doc_type,
                **(extra or {}),
            },
        )
    except Exception:
        logger.exception(
            'doc_extract audit write failed action=%s template_id=%s',
            action.value,
            template_id,
        )
