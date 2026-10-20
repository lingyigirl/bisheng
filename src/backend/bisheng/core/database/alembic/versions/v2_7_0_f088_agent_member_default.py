"""F088: add linsight_agent_member.is_default (auto-carry flag).

Revision ID: f088_agent_member_default
Revises: f087_agent_member
Create Date: 2026-10-16

Members flagged default are auto-checked into new conversations by the client.
Guarded by ``column_exists`` (metadata may have created the column with the
current model shape first — the dual schema-management contract).
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op

from bisheng.core.database.alembic_helpers.online import column_exists

revision: str = "f088_agent_member_default"
down_revision: Union[str, Sequence[str], None] = "f087_agent_member"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TABLE = "linsight_agent_member"
_COLUMN = "is_default"


def upgrade() -> None:
    if not column_exists(_TABLE, _COLUMN):
        op.add_column(
            _TABLE,
            sa.Column(_COLUMN, sa.Integer(), nullable=False, server_default=sa.text("0"), comment="Default-carry flag"),
        )


def downgrade() -> None:
    if column_exists(_TABLE, _COLUMN):
        op.drop_column(_TABLE, _COLUMN)
