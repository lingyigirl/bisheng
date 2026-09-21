"""F060: the term library — one tenant-scoped glossary table.

  * `term_library_term` — a standard term, its aliases, and what it means

Why there is only one table
---------------------------
The feature has a second registered domain object, "对话提示词的术语注入", but it
is a RUNTIME BEHAVIOUR with no storage: the term block is appended to the system
prompt per turn and never written anywhere (contract INV-18). There is nothing
to create for it, which is also why this migration has no column work anywhere
else — unlike F061, this feature touches no existing table.

Why the aliases column is JSON rather than a child table
--------------------------------------------------------
A term has a handful of aliases and they are only ever read as a set (to build
the match automaton) or rewritten whole (to edit the term). A child table would
add a join and a lifecycle — orphan rows, ordering, per-alias constraints — to
model something that is always handled as one value. The cost is that the
per-tenant uniqueness rule (AC-02/AC-03, which spans `standard_term` AND every
alias) cannot be a database constraint, since no index reaches inside JSON. The
service enforces it; `test/term_library/test_term_service.py` holds it down and
is the reason that rule is stated there rather than assumed here.

Why no foreign key on `department_id`
-------------------------------------
A term's department is DISPLAY METADATA and grants nothing (contract INV-17) —
it groups rows in the admin list. A FK would make the column a referential
constraint with a delete behaviour to choose, implying a lifecycle tie that does
not exist: archiving a department must leave its terms alone and merely render
them as ungrouped. The service handles that on write (11506) and the read path
ignores a stale id entirely.

DEV NOTE ON THE TABLE NAME: `TermLibraryTerm.__tablename__` is set explicitly to
`term_library_term`. SQLModel's default would lowercase the class name with no
underscores, producing `termlibraryterm` here — the same trap F061's migration
documents for `assistantlink`.

DDL-only
--------
Per `alembic/AGENTS.md` §5 this revision contains schema DDL only, and no
backfill is needed: the table starts empty.

Revision ID: f060_term_library
Revises: f061_datasource
Create Date: 2026-09-21
"""

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op

from bisheng.core.database.dialect_helpers import (
    JsonType,
    LargeText,
    index_exists,
    table_exists,
    update_time_server_default,
)

revision: str = "f060_term_library"
down_revision: Union[str, Sequence[str], None] = "f061_datasource"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TERM_TABLE = "term_library_term"


def upgrade() -> None:
    # Guarded like every other revision here: an online upgrade runs
    # `create_all(checkfirst=True)` at the CURRENT model shape before replaying
    # revisions, so on a fresh database this table already exists, while an
    # existing deployment has neither it nor its indexes. Both paths must
    # converge on the same end state.
    conn = op.get_bind()

    if not table_exists(conn, _TERM_TABLE):
        op.create_table(
            _TERM_TABLE,
            sa.Column('id', sa.Integer, primary_key=True, autoincrement=True),
            sa.Column('tenant_id', sa.Integer, nullable=False, comment='租户ID'),
            sa.Column('standard_term', sa.String(255), nullable=False,
                      comment='标准词; 租户内唯一'),
            sa.Column('aliases', JsonType, nullable=True,
                      comment='别名数组; 与标准词一同参与租户内唯一性校验'),
            sa.Column('definition', LargeText, nullable=False,
                      comment='释义; 注入对话时的解释正文, 上限 200 字'),
            sa.Column('department_id', sa.Integer, nullable=True,
                      comment='归属部门ID; 仅用于管理页筛选, 不参与鉴权与注入范围 (INV-17)'),
            sa.Column('create_user_id', sa.Integer, nullable=True, comment='创建人ID'),
            sa.Column('create_time', sa.DateTime, nullable=False,
                      server_default=sa.text('CURRENT_TIMESTAMP')),
            sa.Column('update_time', sa.DateTime, nullable=False,
                      server_default=update_time_server_default(conn)),
        )
    if not index_exists(conn, _TERM_TABLE, 'ix_term_library_term_tenant_id'):
        op.create_index('ix_term_library_term_tenant_id', _TERM_TABLE, ['tenant_id'])
    if not index_exists(conn, _TERM_TABLE, 'ix_term_library_term_standard_term'):
        op.create_index('ix_term_library_term_standard_term', _TERM_TABLE, ['standard_term'])
    if not index_exists(conn, _TERM_TABLE, 'ix_term_library_term_department_id'):
        op.create_index('ix_term_library_term_department_id', _TERM_TABLE, ['department_id'])


def downgrade() -> None:
    """Drop the table and its indexes.

    Reversible with no data-loss caveat beyond the terms themselves: nothing
    references this table (there is no inbound FK, by the design above), and
    deleting a term is already a supported operation that leaves history
    readable — injection is a runtime behaviour and is not stored (spec §3).

    Dropping the table drops its indexes with it; the separate index guards are
    only here so a re-run after a partial failure does not error.
    """
    conn = op.get_bind()
    if not table_exists(conn, _TERM_TABLE):
        return
    for name in (
        'ix_term_library_term_department_id',
        'ix_term_library_term_standard_term',
        'ix_term_library_term_tenant_id',
    ):
        if index_exists(conn, _TERM_TABLE, name):
            op.drop_index(name, table_name=_TERM_TABLE)
    op.drop_table(_TERM_TABLE)
