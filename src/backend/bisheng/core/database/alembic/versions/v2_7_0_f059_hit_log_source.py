"""F059: mark the origin of a sensitive-word hit (`source`: tenant | platform).

`sensitive_word_hit_log` was introduced by F056 and only ever recorded hits from
the *tenant* policy. F059 adds a platform-wide, super-admin-maintained library
that is enforced across every tenant, so a hit row can now come from either
layer — and the two must be distinguishable in the log.

Why the distinction matters to tenants
--------------------------------------
A tenant administrator may see that a block happened but must NOT learn which
word caused it (release-contract INV-10): the platform library is authoritative
content, and a visible word list can be reverse-engineered by probing the chat
endpoint. The `source` column is what lets the read path decide to blank the
`hits` payload (see the F059 spec AC-18) while still showing the tenant that a
block occurred, so they can tell "the guardrail fired" apart from "the system is
broken".

Why `server_default` and NOT a data-update
------------------------------------------
Every pre-existing row necessarily came from the tenant layer — the platform
layer did not exist before this release. `server_default='tenant'` therefore
describes those rows correctly AND backfills them as part of the DDL, which is
the only data effect a revision is permitted (`alembic/AGENTS.md` §5). Writing an
explicit `UPDATE` to backfill would be a data migration and belongs in
`scripts/`, not here.

The column is NULLABLE with a server-side default rather than NOT NULL: rows
inserted by an older application process during a rolling deploy would otherwise
fail their INSERT, and a hit-log write is best-effort (F056 AC-16) — a failed
audit write must never break a conversation.

While this column is nullable, readers must treat NULL as `tenant` — the ORM
model and the hit-log service both apply that fallback, so NULL is only ever
transient (an old process still running mid-deploy).

Revision ID: f059_hit_log_source
Revises: f056_knowledge_department
Create Date: 2026-09-17
"""

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op

from bisheng.core.database.dialect_helpers import column_exists

revision: str = "f059_hit_log_source"
down_revision: Union[str, Sequence[str], None] = "f056_knowledge_department"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_HIT_LOG_TABLE = "sensitive_word_hit_log"
_SOURCE_COLUMN = "source"


def upgrade() -> None:
    # Guarded because model-created tables are built at their CURRENT model shape
    # by `create_all(checkfirst=True)`: on a fresh database the table may already
    # carry this column, while an existing deployment does not. Both paths must
    # converge on the same end state.
    conn = op.get_bind()
    if not column_exists(conn, _HIT_LOG_TABLE, _SOURCE_COLUMN):
        op.add_column(
            _HIT_LOG_TABLE,
            sa.Column(
                _SOURCE_COLUMN,
                sa.String(16),
                nullable=True,
                server_default=sa.text("'tenant'"),
                comment="命中来源: tenant=租户策略, platform=平台强制词库; NULL 等同 tenant",
            ),
        )


def downgrade() -> None:
    # NOTE — dropping this column is LOSSY and irreversible in effect: the origin
    # of each hit cannot be reconstructed from any other column (`hits` stores
    # only the word summary, and the two layers' words are not otherwise
    # distinguishable). Confirm that no platform-sourced rows exist before
    # downgrading, or that losing that attribution is acceptable.
    conn = op.get_bind()
    if column_exists(conn, _HIT_LOG_TABLE, _SOURCE_COLUMN):
        op.drop_column(_HIT_LOG_TABLE, _SOURCE_COLUMN)
