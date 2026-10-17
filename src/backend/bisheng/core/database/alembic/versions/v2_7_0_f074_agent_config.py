"""Add task collaborator configuration snapshots.

Revision ID: f074_agent_config
Revises: f064_field_comment_override
"""

import sqlalchemy as sa
from alembic import op

from bisheng.core.database.alembic_helpers.online import column_exists
from bisheng.core.database.dialect_helpers import JsonType

revision = "f074_agent_config"
down_revision = "f064_field_comment_override"
branch_labels = None
depends_on = None


def upgrade():
    if not column_exists("linsight_session_version", "agent_config"):
        op.add_column("linsight_session_version", sa.Column("agent_config", JsonType(), nullable=True))


def downgrade():
    # Destructive: application rollbacks should retain this nullable column.
    if column_exists("linsight_session_version", "agent_config"):
        op.drop_column("linsight_session_version", "agent_config")
