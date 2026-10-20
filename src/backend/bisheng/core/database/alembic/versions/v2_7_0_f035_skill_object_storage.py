"""Add the nullable object-storage version pointer; data migration is manual."""

import sqlalchemy as sa
from alembic import op

from bisheng.core.database.alembic_helpers.online import column_exists, table_exists

revision = "f035_skill_object_storage"
down_revision = "f074_recovery_scan_index"
branch_labels = None
depends_on = None


def upgrade():
    if table_exists("linsight_skill") and not column_exists("linsight_skill", "content_hash"):
        op.add_column("linsight_skill", sa.Column("content_hash", sa.String(64), nullable=True))


def downgrade():
    if table_exists("linsight_skill") and column_exists("linsight_skill", "content_hash"):
        op.drop_column("linsight_skill", "content_hash")
