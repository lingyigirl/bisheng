"""Index bounded recovery discovery by status and task id."""

from alembic import op

from bisheng.core.database.dialect_helpers import index_exists

revision = "f074_recovery_scan_index"
down_revision = "f088_agent_member_default"
branch_labels = None
depends_on = None

_TABLE = "linsight_session_version"
_INDEX = "ix_linsight_session_version_status_id"


def upgrade():
    if not index_exists(op.get_bind(), _TABLE, _INDEX):
        op.create_index(_INDEX, _TABLE, ["status", "id"])


def downgrade():
    if index_exists(op.get_bind(), _TABLE, _INDEX):
        op.drop_index(_INDEX, table_name=_TABLE)
