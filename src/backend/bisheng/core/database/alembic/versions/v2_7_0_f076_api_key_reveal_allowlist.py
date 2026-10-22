"""F076 increment: API key reveal ciphertext + per-key agent allowlist.

Revision ID: f076_api_key_reveal_allowlist
Revises: f035_skill_object_storage

Both columns are nullable and have no server default, so adding them is safe on
MySQL and DM8 and does not rewrite existing rows:

- ``token_ciphertext``: Fernet ciphertext used only by the admin reveal action.
  Existing keys keep NULL and stay non-revealable.
- ``allowed_apps``: per-key agent allowlist (JSON array ``[{type,id}]``).
  NULL means "unrestricted" (pre-existing behaviour); ``[]`` means "deny all
  agents" (spec AC-42 / INV-27).

Do not drop ``allowed_apps`` on rollback: the old code ignores the column, and
dropping it would silently widen every key back to account-wide visibility.
"""

import sqlalchemy as sa
from alembic import op

from bisheng.core.database.alembic_helpers.online import column_exists, table_exists
from bisheng.core.database.dialect_helpers import JsonType

revision = "f076_api_key_reveal_allowlist"
down_revision = "f035_skill_object_storage"
branch_labels = None
depends_on = None

_TABLE = "api_credential"


def upgrade():
    # Fresh installations create the table from the current SQLModel shape via
    # create_all(), so both columns may already exist; the guards keep this
    # revision idempotent on every environment.
    if not table_exists(_TABLE):
        return
    if not column_exists(_TABLE, "token_ciphertext"):
        op.add_column(
            _TABLE,
            sa.Column(
                "token_ciphertext",
                sa.String(512),
                nullable=True,
                comment="Fernet 密文(仅管理员显式查看时解密);NULL=存量密钥不可查看",
            ),
        )
    if not column_exists(_TABLE, "allowed_apps"):
        op.add_column(
            _TABLE,
            sa.Column(
                "allowed_apps",
                JsonType(),
                nullable=True,
                comment="每 Key 智能体白名单 [{type,id}];NULL=不限制,[]=全部拒绝",
            ),
        )


def downgrade():
    # Deliberately non-destructive: dropping allowed_apps would widen every key
    # back to account-wide visibility, and the old code ignores both columns.
    # Operators who really need the columns gone must do it as an explicit,
    # audited DBA step.
    pass
