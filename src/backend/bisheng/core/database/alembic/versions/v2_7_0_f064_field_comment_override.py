"""F064: a per-field manual override column on ``db_datasource_table``.

One nullable JSON column, ``column_overrides``, holding ``{"字段名": 人工说明}``.
It is the field-level counterpart of ``business_desc``: ``areplace_snapshot``
owns ``structure_snapshot`` and ``table_comment`` and never mentions this
column, so a re-collect cannot clobber an override -- the same structural
guarantee AC-12 already gives the business description, one level down.

Why a column rather than a table, and why not inside the snapshot
-----------------------------------------------------------------
The snapshot is REPLACED wholesale on every re-collect, so an override stored
inside it would be lost silently, at the next collect, with nothing to report
it. A neighbouring column has the row's own lifetime -- deleting the table
takes its overrides with it -- and costs no join on the read path that builds
the schema prompt.

Why nullable with no server_default
-----------------------------------
NULL means "no override anywhere on this table", which is every existing row.
A ``server_default`` of ``'{}'`` would mean the same thing to a reader, while
also making "never touched" indistinguishable from "every override removed" --
the write path normalises empty input back to NULL precisely to keep those two
apart (F064 decision 5), so the DDL must not reintroduce the ambiguity.

DDL-only
--------
Per ``alembic/AGENTS.md`` §5 this revision contains schema DDL only. The column
starts NULL on every existing row and nothing reads it until an admin writes
one, so no data effect is needed -- or permitted.

Revision ID: f064_field_comment_override
Revises: f063_term_standard_term_unique
Create Date: 2026-09-23
"""

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op

from bisheng.core.database.dialect_helpers import JsonType, column_exists

revision: str = "f064_field_comment_override"
down_revision: Union[str, Sequence[str], None] = "f063_term_standard_term_unique"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TABLE = "db_datasource_table"
_COLUMN = "column_overrides"


def upgrade() -> None:
    # Guarded like every neighbouring revision. An online upgrade runs
    # `create_all(checkfirst=True)` at the CURRENT model shape before replaying
    # revisions, so on a fresh database this column already exists, while an
    # upgraded deployment has the table without it. Both paths must converge.
    conn = op.get_bind()
    if not column_exists(conn, _TABLE, _COLUMN):
        op.add_column(
            _TABLE,
            sa.Column(
                _COLUMN,
                JsonType,
                nullable=True,
                comment='字段级人工订正 {"字段名": 人工说明}; 重采集不得覆盖; 空值键由写入侧丢弃',
            ),
        )


def downgrade() -> None:
    conn = op.get_bind()
    if column_exists(conn, _TABLE, _COLUMN):
        op.drop_column(_TABLE, _COLUMN)
