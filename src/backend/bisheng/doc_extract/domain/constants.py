"""F058 shared constants that land in ``audit_log.action``.

Same reasoning as ``tenant/domain/constants.py``: using an enum member rather
than a bare string is what prevents the "action name typo silently hides the
audit row" class of bug -- a row whose action is misspelled still writes, but
never matches the UI whitelist and so becomes invisible with no error.
"""

from __future__ import annotations

from enum import Enum


class DocExtractAuditAction(str, Enum):
    """Structured ``audit_log.action`` values owned by F058.

    Emitted through ``AuditLogDao.ainsert_v2`` and surfaced to operators via
    ``_UI_VISIBLE_V2_ACTIONS`` (backend) plus the mirror list in the platform's
    ``controllers/API/log.ts``. Adding a member here without adding it to both
    whitelists means the row is written but unreachable from the audit page.
    """

    TEMPLATE_CREATE = 'doc_extract.template.create'
    TEMPLATE_UPDATE = 'doc_extract.template.update'
    TEMPLATE_DELETE = 'doc_extract.template.delete'
    TEMPLATE_ENABLE = 'doc_extract.template.enable'
    TEMPLATE_DISABLE = 'doc_extract.template.disable'


#: Every action this module emits, in the order they appear in the audit page
#: filter. Kept as a tuple so the lockstep check in tests can iterate it.
DOC_EXTRACT_AUDIT_ACTIONS = tuple(action.value for action in DocExtractAuditAction)
