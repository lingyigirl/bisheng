"""Add fenced execution metadata for crash recovery.

Revision ID: f074_recovery
Revises: f074_agent_config
"""

import sqlalchemy as sa
from alembic import op

from bisheng.core.database.alembic_helpers.online import column_exists
from bisheng.core.database.dialect_helpers import JsonType

revision = "f074_recovery"
down_revision = "f074_agent_config"
branch_labels = None
depends_on = None


def upgrade():
    if not column_exists("linsight_session_version", "recovery_epoch"):
        op.add_column(
            "linsight_session_version", sa.Column("recovery_epoch", sa.Integer(), nullable=False, server_default="0")
        )
    if not column_exists("linsight_session_version", "recovery_state"):
        op.add_column("linsight_session_version", sa.Column("recovery_state", JsonType(), nullable=True))


def downgrade():
    for name in ("recovery_state", "recovery_epoch"):
        if column_exists("linsight_session_version", name):
            op.drop_column("linsight_session_version", name)
