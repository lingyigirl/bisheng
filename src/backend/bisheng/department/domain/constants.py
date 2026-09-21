"""F062 department-tree audit action names.

Single source of truth for the strings written into ``audit_log.action`` by the
marker-flip and node-move flows. The enum exists for the reason
``TenantAuditAction`` documents: ``action`` is a free-text column, so a typo
writes a row that is accepted, stored, and invisible to every auditor query —
a silent loss with no error anywhere.

Two actions only, and deliberately so. ``audit_log`` already carries the FGA
tuple churn for department changes via ``DepartmentChangeHandler``; duplicating
those would make the trail harder, not easier, to read. What is NEW in F062 is
that a single administrative action can change the visibility of many libraries
at once, which is what these two rows record.
"""

from __future__ import annotations

from enum import Enum


class DepartmentAuditAction(str, Enum):
    """Structured ``audit_log.action`` values owned by F062."""

    #: A node's 机构/部门 marker moved. `metadata` carries from/to markers plus
    #: the affected department and library counts shown in the confirm dialog.
    NODE_TYPE_CHANGED = "dept.node_type_changed"

    #: A node was moved to a new parent. `metadata` carries both parent ids plus
    #: the same impact counts — a move relocates an entire subtree between org
    #: domains, so its blast radius is at least that of a marker flip.
    NODE_MOVED = "dept.node_moved"
