"""F057: department ownership for organization knowledge libraries (type=0/1).

Adds `knowledge.department_id` — a NULLABLE int naming the single department a
library belongs to. This is the storage half of "department ownership": the
visibility rule itself is evaluated at READ time (see below), so no FGA tuples
are written by this feature and this revision is pure DDL.

Why nullable, and what NULL means
---------------------------------
NULL = "unclassified" (未分类), NOT "global". The read-time rule ADDS an
ownership-based grant on top of the existing FGA-derived visibility
(`可见 = FGA 授权 OR 部门链上的知识库`), so an unclassified library keeps exactly
the visibility it has today. That is what makes this change safe for existing
rows: the `server_default` fill leaves every legacy library unclassified, and
their effective access is unchanged.

Ownership direction (deliberately asymmetric with the platform tree)
-------------------------------------------------------------------
A library owned by department D is visible to members of D *and every descendant
of D* — the ancestors' libraries flow DOWN to descendants. The platform browser
tree does the mirror image: clicking a node shows that node's subtree. Both
directions are derived from `department.path` (materialized `/1/2/3/`, LAST
segment is the node's OWN id — despite the stale comment at
`department_service.py:1512` claiming otherwise).

FGA cannot express the down-flow on its own: `department.member` is a flat
`{'this': {}}` relation with no inheritance AND no descent
(`core/openfga/authorization_model.py:237`), and the one relation that DOES
inherit, `admin`, walks upward via `tupleToUserset(parent, admin)`. Writing an
expanded subtree of tuples instead would leak on reparent and miss newly created
children — there is no department-change hook that recomputes resource grants
anywhere in the codebase. Read-time evaluation via path-prefix comparison is
immune to both, because `path` is rewritten on move
(`update_paths_batch`, `database/models/department.py:529-556`).

ON DELETE — deliberately NO foreign key
----------------------------------------
The requirement is "deleting a department returns its libraries to unclassified"
(SET NULL semantics). This revision does NOT add a FK to achieve it, for three
reasons:

  1. `ALTER TABLE ... ADD CONSTRAINT FOREIGN KEY` on an EXISTING table has no
     precedent in this repo — every FK here was created inside `create_table`
     (f021, f026). Introducing the first one on `knowledge` — a large, hot,
     multi-tenant table — is a schema-wide risk not worth taking for a niche
     feature.
  2. DM8 is Oracle-compatible; `ON DELETE SET NULL` support and identifier
     behaviour on an `ALTER TABLE ADD CONSTRAINT` need DB-team verification
     (`env.py` already patches several DM8/`dmSQLAlchemy` reflection bugs).
  3. It is not needed. The reset is performed by application code when the
     platform deletes a department, and a dangling `department_id` degrades
     safely: the read-time lookup finds no department, no ownership grant is
     produced, and the library is simply unclassified. Only the ORPHANED ID
     LINGERS — harmless for behaviour, but it must be cleaned outside a
     migration (the DDL-only rule below forbids doing it here).

If the DB team decides a DB-level FK is required, add it in a FOLLOW-UP revision
after the cleanup of dangling ids has run out-of-band.

DDL-only
--------
Per `alembic/AGENTS.md` §5, `upgrade()` contains schema DDL only. The
`server_default` row-fill is the sole permitted data effect; the dangling-id
cleanup for pre-existing data is an operational prerequisite handled by a
`scripts/` job, not by this revision.

Revision ID: f057_knowledge_department
Revises: f055_kb_effective_weight
Create Date: 2026-09-15
"""

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op

from bisheng.core.database.dialect_helpers import column_exists, index_exists

# NOTE ON THE NAME: the revision id still carries the feature's ORIGINAL code
# (`f056`), which collided with the unrelated F056-llm-guardrail and has since
# been renamed to F057 everywhere else. This id is deliberately NOT renamed:
# the revision was already applied on the shared development database, whose
# `alembic_version` row reads `f056_knowledge_department`. Changing the id here
# would make `alembic upgrade head` fail with "Can't locate revision identified
# by 'f056_knowledge_department'" on that database. The id is an opaque key —
# only the filename and comments track the feature code.
revision: str = "f056_knowledge_department"
down_revision: Union[str, Sequence[str], None] = "f055_kb_effective_weight"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_KNOWLEDGE_TABLE = "knowledge"
_DEPARTMENT_COLUMN = "department_id"
_DEPARTMENT_INDEX = "ix_knowledge_department_id"


def upgrade() -> None:
    # NOTE: `dialect_helpers.column_exists` / `index_exists` take (conn, table,
    # name) — unlike the two-arg `alembic_helpers.online.column_exists`.
    conn = op.get_bind()

    # Nullable on purpose (see module docstring): NULL = unclassified, which
    # preserves today's visibility for every existing row. No server_default —
    # a DEFAULT would make "unclassified" ambiguous with "defaulted to dept 0".
    if not column_exists(conn, _KNOWLEDGE_TABLE, _DEPARTMENT_COLUMN):
        op.add_column(
            _KNOWLEDGE_TABLE,
            sa.Column(
                "department_id",
                sa.Integer(),
                nullable=True,
                comment="F057 所属部门ID 归属:所属部门及其下级部门可见;NULL=未分类不参与部门可见性",
            ),
        )

    # Index for the read-time ownership lookup (department_id -> libraries) and
    # the platform tree filter. Guarded because model-created tables may already
    # carry it — `create_all()` creates missing tables at current model shape
    # and never alters existing ones, so both paths must tolerate either state.
    #
    # Use the shared `index_exists` helper rather than `sa.inspect(op.get_bind())`:
    # under `alembic upgrade --sql` (offline/dry-run, which env.py performs) the
    # bind is a MockConnection with no database to reflect, and raw inspection
    # raises `NoInspectionAvailable`. The helpers degrade correctly there.
    if not index_exists(conn, _KNOWLEDGE_TABLE, _DEPARTMENT_INDEX):
        op.create_index(_DEPARTMENT_INDEX, _KNOWLEDGE_TABLE, [_DEPARTMENT_COLUMN])


def downgrade() -> None:
    conn = op.get_bind()
    if index_exists(conn, _KNOWLEDGE_TABLE, _DEPARTMENT_INDEX):
        op.drop_index(_DEPARTMENT_INDEX, table_name=_KNOWLEDGE_TABLE)
    if column_exists(conn, _KNOWLEDGE_TABLE, _DEPARTMENT_COLUMN):
        op.drop_column(_KNOWLEDGE_TABLE, _DEPARTMENT_COLUMN)
