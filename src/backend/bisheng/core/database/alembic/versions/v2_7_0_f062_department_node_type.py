"""F062: the organization-tree node marker — 机构 (org) vs 部门 (department).

Adds `department.is_department` — a NOT NULL integer flag whose ONLY job is to
say whether a node is TRANSPARENT to its ancestors. `1` = 部门 (transparent),
`0` = 机构 (opaque, a barrier). The visibility rule itself is evaluated at READ
time (see `PermissionService._implicit_department_library_level`), so no FGA
tuples are written by this feature and this revision is pure DDL.

Why the default is 0 (机构), and why that is the whole upgrade story
--------------------------------------------------------------------
With EVERY node marked 机构, the new rule collapses EXACTLY onto F057's existing
rule — each node's domain is itself alone, so the visible set is "self + all
ancestors", which is precisely F057's `owning.path`-is-a-prefix-of-user's-path
test. That is an identity, not an approximation, and it is what makes this
revision safe on a live database: `server_default` fills every legacy row with
the value that changes nothing.

Marking everything 部门, or guessing by depth, both OPEN the entire tenant to
everyone the moment the migration runs — irreversibly, with no way to audit who
saw what in the window before anyone noticed. Hence the "opaque" default: the
feature ships INERT and is switched on deliberately, node by node, by whoever
owns the org chart.

The cost is that the feature does not "just work" after deployment — the org
tree must be annotated before any effect is observable. That is an accepted,
documented delivery prerequisite, not a defect.

Why the column is named `is_department` and not `is_org`
--------------------------------------------------------
The business requirement numbers the values "0 = 机构, 1 = 部门". A field named
`is_org` would therefore read `is_org = 0` for "this IS an org" — the boolean
prefix inverted against the value, which is exactly the shape of bug that gets
written and then misread. `is_department = 0` reads correctly ("not a department
→ an org") and matches the house style of this table, where `is_tenant_root` and
`is_deleted` are also integers with 1 = true.

Why no index
------------
Nothing queries this column. The visibility rule loads the tenant's whole
department table once and derives `id -> nearest-org-path` in memory (the org
trees this targets are ~500-1000 nodes), and the marker is consumed only during
that derivation. An index here would be maintained forever and read never.

DDL-only
--------
Per `alembic/AGENTS.md` §5, `upgrade()` contains schema DDL only. The
`server_default` row-fill is the sole permitted data effect; there is no data
backfill because the default IS the backfill.

Revision ID: f062_department_node_type
Revises: f060_term_library
Create Date: 2026-09-21
"""

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op

from bisheng.core.database.dialect_helpers import column_exists

revision: str = "f062_department_node_type"
down_revision: Union[str, Sequence[str], None] = "f060_term_library"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_DEPARTMENT_TABLE = "department"
_IS_DEPARTMENT_COLUMN = "is_department"


def upgrade() -> None:
    # Guarded like every other revision here: an online upgrade runs
    # `create_all(checkfirst=True)` at the CURRENT model shape before replaying
    # revisions, so on a fresh database this column already exists, while an
    # existing deployment has neither it nor any default. Both paths must
    # converge on the same end state.
    #
    # NOTE: `dialect_helpers.column_exists` takes (conn, table, name) — unlike
    # the two-arg `alembic_helpers.online.column_exists`.
    conn = op.get_bind()

    if not column_exists(conn, _DEPARTMENT_TABLE, _IS_DEPARTMENT_COLUMN):
        op.add_column(
            _DEPARTMENT_TABLE,
            sa.Column(
                "is_department",
                sa.Integer(),
                nullable=False,
                server_default=sa.text("0"),
                comment="F062 是否部门:1=部门(对上级透明),0=机构(不透明,可见性隔离边界);默认0=升级零行为变化",
            ),
        )


def downgrade() -> None:
    conn = op.get_bind()
    if column_exists(conn, _DEPARTMENT_TABLE, _IS_DEPARTMENT_COLUMN):
        op.drop_column(_DEPARTMENT_TABLE, _IS_DEPARTMENT_COLUMN)
