"""F060 (code review M3): make `standard_term` unique per tenant in the database.

Why this exists
---------------
`_assert_unique` in `term_service` is a read-then-insert check: it reads the whole
library, compares, and inserts. Two admins creating the same term at the same
moment — or one admin double-clicking 保存 — both read "not taken" and both
insert, producing two terms claiming one name. Spec §3 asks for the opposite
(the tenant's uniqueness constraint is the authority, the loser fails visibly,
and no duplicate row appears), and AC-02 says the standard term is unique within
a tenant.

F060 shipped with a plain `ix_term_library_term_standard_term`, and both the
feature's own migration and F061's sibling endpoint recorded the check-then-insert
shape as deliberate ("the row index is not [unique], so this is the check"). That
reasoning is sound for the ALIAS half of the rule — aliases live inside a JSON
column and no index reaches into them, so the service really is the only
enforcement available. It does not apply to `standard_term`, which is an ordinary
indexable column. This revision closes that half.

Scope, stated precisely
-----------------------
The index is `(tenant_id, standard_term)`, matching the uniqueness the service
checks (per tenant). It is **case-SENSITIVE**, which is what MySQL and DM8 give
for free and what the existing collation already decides; the service's
`casefold()` comparison remains the authority for names differing only in case.
A generated/functional index on the folded value would close that too, but it is
not expressible portably across MySQL and DM8, and getting it wrong on DM8 is
worse than leaving the narrower guarantee explicit here.

Operational prerequisite
------------------------
Adding this fails if the tenant already holds two rows with the same standard
term — the code review's M3 racetrack may have produced such rows in a live
deployment. Per `alembic/AGENTS.md` §5 that cleanup is **not** done here (a
revision may not inspect or rewrite existing rows): the deploy fails loudly, the
duplicates are resolved out-of-band, and the migration is re-run. That is the
documented and intended signal, not something to paper over with an inline dedup.

DDL-only
--------
Schema DDL only: one `drop_index`, one `create_index`. No SELECT, no backfill, no
conditional-on-data branching.

Revision ID: f063_term_standard_term_unique
Revises: f062_department_node_type
Create Date: 2026-09-21
"""

from collections.abc import Sequence
from typing import Union

from alembic import op

from bisheng.core.database.dialect_helpers import index_exists

revision: str = "f063_term_standard_term_unique"
down_revision: Union[str, Sequence[str], None] = "f062_department_node_type"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TERM_TABLE = "term_library_term"
_PLAIN_INDEX = "ix_term_library_term_standard_term"
_UNIQUE_INDEX = "uk_term_library_term_tenant_standard"


def upgrade() -> None:
    # Both statements are guarded, for the same reason the F060 revision is: an
    # online upgrade runs `create_all(checkfirst=True)` at the CURRENT model shape
    # before replaying revisions, so on a fresh database the model's own unique
    # index already exists (and the plain one does not), while an existing
    # deployment is the other way round. Both paths must converge.
    conn = op.get_bind()

    if index_exists(conn, _TERM_TABLE, _PLAIN_INDEX):
        op.drop_index(_PLAIN_INDEX, table_name=_TERM_TABLE)

    if not index_exists(conn, _TERM_TABLE, _UNIQUE_INDEX):
        op.create_index(
            _UNIQUE_INDEX,
            _TERM_TABLE,
            ['tenant_id', 'standard_term'],
            unique=True,
        )


def downgrade() -> None:
    """Restore the plain index, dropping the unique one.

    Reversible without data loss: a unique index is strictly stronger than the
    plain one it replaces, so every row that satisfies the constraint also
    satisfies the looser index. The reverse is not true — which is the whole
    point of the upgrade.
    """
    conn = op.get_bind()

    if index_exists(conn, _TERM_TABLE, _UNIQUE_INDEX):
        op.drop_index(_UNIQUE_INDEX, table_name=_TERM_TABLE)

    if not index_exists(conn, _TERM_TABLE, _PLAIN_INDEX):
        op.create_index(_PLAIN_INDEX, _TERM_TABLE, ['standard_term'])
