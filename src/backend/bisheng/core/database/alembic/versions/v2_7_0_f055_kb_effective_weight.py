"""F055: risk-compliance KB — effective dates on files, weight/priority on KBs.

Adds KnowledgeFile.effective_date/expire_date (nullable; NULL = unlimited, spec
AC-06) and Knowledge.weight (default 1.0) / Knowledge.priority (default 0). DDL
only — the server_default fill is the only data effect: existing KB rows get
1.0/0 (legacy ranking behaviour, AC-11), existing files stay NULL = always
effective (legacy retrieval behaviour, design decision 2).

Revision ID: f055_kb_effective_weight
Revises: f053_eval_compare_group
Create Date: 2026-09-06
"""

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op

from bisheng.core.database.dialect_helpers import column_exists

revision: str = "f055_kb_effective_weight"
down_revision: Union[str, Sequence[str], None] = "f053_eval_compare_group"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    if not column_exists(conn, "knowledge", "weight"):
        op.add_column(
            "knowledge",
            sa.Column(
                "weight",
                sa.Float(),
                nullable=False,
                server_default=sa.text("1"),
                comment="F055 知识库权重 检索排序用",
            ),
        )
    if not column_exists(conn, "knowledge", "priority"):
        op.add_column(
            "knowledge",
            sa.Column(
                "priority",
                sa.Integer(),
                nullable=False,
                server_default=sa.text("0"),
                comment="F055 知识库调用优先级 0=普通 1=优先",
            ),
        )
    if not column_exists(conn, "knowledgefile", "effective_date"):
        op.add_column(
            "knowledgefile",
            sa.Column("effective_date", sa.DateTime(), nullable=True, comment="F055 生效日期"),
        )
    if not column_exists(conn, "knowledgefile", "expire_date"):
        op.add_column(
            "knowledgefile",
            sa.Column("expire_date", sa.DateTime(), nullable=True, comment="F055 失效日期"),
        )


def downgrade() -> None:
    op.drop_column("knowledgefile", "expire_date")
    op.drop_column("knowledgefile", "effective_date")
    op.drop_column("knowledge", "priority")
    op.drop_column("knowledge", "weight")
