"""F087: create linsight_agent_member (per-user saved collaborator templates).

Revision ID: f087_agent_member
Revises: f074_recovery
Create Date: 2026-10-16

Chained onto the current head (f074_recovery) so the single-head invariant
holds: a fork off a mid-chain ancestor would create a second head and break
``alembic upgrade head`` (singular) at startup. Guarded by ``table_exists``
because SQLModel metadata may have created the table first (dual contract:
metadata owns whole-table creation, Alembic owns changes to existing tables).
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op

from bisheng.core.database.dialect_helpers import JsonType, index_exists, table_exists

revision: str = "f087_agent_member"
down_revision: Union[str, Sequence[str], None] = "f074_recovery"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TABLE = "linsight_agent_member"


def upgrade() -> None:
    conn = op.get_bind()
    if not table_exists(conn, _TABLE):
        op.create_table(
            _TABLE,
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("tenant_id", sa.Integer(), nullable=False, server_default=sa.text("1"), comment="Tenant ID"),
            sa.Column("user_id", sa.Integer(), nullable=False, comment="Owner user id"),
            sa.Column("name", sa.String(length=64), nullable=False, comment="Member display name"),
            sa.Column("description", sa.Text(), nullable=False, comment="Role description"),
            sa.Column("system_prompt", sa.Text(), nullable=False, comment="System prompt (<=12000)"),
            sa.Column("model_id", sa.String(length=20), nullable=True, comment="Preferred model id; NULL = inherit"),
            sa.Column("skill_names", JsonType, nullable=True, comment="Selected skill names"),
            sa.Column("tool_refs", JsonType, nullable=True, comment="Selected tool ids"),
            sa.Column("connector_refs", JsonType, nullable=True, comment="Selected connector ids"),
            sa.Column("create_time", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
            sa.Column("update_time", sa.DateTime(), nullable=True),
            sa.UniqueConstraint("tenant_id", "user_id", "name", name="uq_linsight_agent_member_tenant_user_name"),
        )

    if not index_exists(conn, _TABLE, "idx_linsight_agent_member_tenant_id"):
        op.create_index("idx_linsight_agent_member_tenant_id", _TABLE, ["tenant_id"])
    if not index_exists(conn, _TABLE, "idx_linsight_agent_member_user_id"):
        op.create_index("idx_linsight_agent_member_user_id", _TABLE, ["user_id"])


def downgrade() -> None:
    conn = op.get_bind()
    if index_exists(conn, _TABLE, "idx_linsight_agent_member_user_id"):
        op.drop_index("idx_linsight_agent_member_user_id", table_name=_TABLE)
    if index_exists(conn, _TABLE, "idx_linsight_agent_member_tenant_id"):
        op.drop_index("idx_linsight_agent_member_tenant_id", table_name=_TABLE)
    if table_exists(conn, _TABLE):
        op.drop_table(_TABLE)
