"""F053: add compare_group_id to evaluation for dual-version compare runs.

Two evaluations created in one compare flow share a uuid in ``compare_group_id``
(single-version evaluations stay NULL). No data migration — historical rows are
not backfilled into groups.

Revision ID: f053_eval_compare_group
Revises: f048_merge_f046_f047_heads
Create Date: 2026-09-05
"""

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op

from bisheng.core.database.dialect_helpers import column_exists

revision: str = "f053_eval_compare_group"
down_revision: Union[str, Sequence[str], None] = "f048_merge_f046_f047_heads"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    if not column_exists(conn, "evaluation", "compare_group_id"):
        op.add_column(
            "evaluation",
            sa.Column(
                "compare_group_id",
                sa.String(36),
                nullable=True,
                comment="F053 版本对比评测分组: 同组两条评测共享 uuid, 单版本评测为 NULL",
            ),
        )
        op.create_index("idx_evaluation_compare_group", "evaluation", ["compare_group_id"])


def downgrade() -> None:
    op.drop_index("idx_evaluation_compare_group", table_name="evaluation")
    op.drop_column("evaluation", "compare_group_id")
