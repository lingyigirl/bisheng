"""F068: workflow dialog turn memory and layered summaries.

Revision ID: f068_chat_turn_memory
Revises: f091_knowledge_graph
Create Date: 2026-10-22
"""

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op

from bisheng.core.database.dialect_helpers import (
    LargeText,
    column_exists,
    index_exists,
    table_exists,
)

revision: str = "f068_chat_turn_memory"
down_revision: Union[str, Sequence[str], None] = "f091_knowledge_graph"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    if not column_exists(conn, "chatmessage", "turn_id"):
        op.add_column("chatmessage", sa.Column("turn_id", sa.String(128), nullable=True))
    if not index_exists(conn, "chatmessage", "ix_chatmessage_turn_id"):
        op.create_index("ix_chatmessage_turn_id", "chatmessage", ["turn_id"])
    if not index_exists(conn, "chatmessage", "idx_chatmessage_chat_turn_id"):
        op.create_index(
            "idx_chatmessage_chat_turn_id",
            "chatmessage",
            ["chat_id", "turn_id", "id"],
        )

    if not column_exists(conn, "message_session", "active_turn_id"):
        op.add_column("message_session", sa.Column("active_turn_id", sa.String(128), nullable=True))
    if not column_exists(conn, "message_session", "active_turn_started_at"):
        op.add_column(
            "message_session",
            sa.Column("active_turn_started_at", sa.DateTime(), nullable=True),
        )
    if not index_exists(conn, "message_session", "ix_message_session_active_turn_id"):
        op.create_index(
            "ix_message_session_active_turn_id",
            "message_session",
            ["active_turn_id"],
        )

    if not table_exists(conn, "chat_turn"):
        op.create_table(
            "chat_turn",
            sa.Column("turn_id", sa.String(128), primary_key=True),
            sa.Column("chat_id", sa.String(64), nullable=False),
            sa.Column("flow_id", sa.String(64), nullable=False),
            sa.Column("user_id", sa.BigInteger(), nullable=False),
            sa.Column("tenant_id", sa.BigInteger(), nullable=False),
            sa.Column("turn_no", sa.Integer(), nullable=False),
            sa.Column("status", sa.String(16), nullable=False, server_default="running"),
            sa.Column("question_text", LargeText, nullable=True),
            sa.Column("answer_text", LargeText, nullable=True),
            sa.Column("question_message_id", sa.BigInteger(), nullable=True),
            sa.Column("answer_message_id", sa.BigInteger(), nullable=True),
            sa.Column("error_code", sa.String(64), nullable=True),
            sa.Column(
                "start_time",
                sa.DateTime(),
                nullable=False,
                server_default=sa.text("CURRENT_TIMESTAMP"),
            ),
            sa.Column("finish_time", sa.DateTime(), nullable=True),
            sa.UniqueConstraint("chat_id", "turn_no", name="uk_chat_turn_chat_no"),
            mysql_charset="utf8mb4",
            mysql_collate="utf8mb4_unicode_ci",
        )
    for index_name, columns in (
        ("ix_chat_turn_chat_id", ["chat_id"]),
        ("ix_chat_turn_flow_id", ["flow_id"]),
        ("ix_chat_turn_user_id", ["user_id"]),
        ("ix_chat_turn_tenant_id", ["tenant_id"]),
        ("ix_chat_turn_status", ["status"]),
        ("ix_chat_turn_start_time", ["start_time"]),
        ("idx_chat_turn_chat_status_no", ["chat_id", "status", "turn_no"]),
        ("idx_chat_turn_chat_user_tenant_no", ["chat_id", "user_id", "tenant_id", "turn_no"]),
        ("idx_chat_turn_flow_status", ["flow_id", "status"]),
    ):
        if not index_exists(conn, "chat_turn", index_name):
            op.create_index(index_name, "chat_turn", columns)
    if not column_exists(conn, "chat_turn", "turn_token_count"):
        op.add_column(
            "chat_turn",
            sa.Column("turn_token_count", sa.Integer(), nullable=False, server_default="0"),
        )

    if not table_exists(conn, "chat_turn_summary"):
        op.create_table(
            "chat_turn_summary",
            sa.Column("summary_id", sa.String(64), primary_key=True),
            sa.Column("chat_id", sa.String(64), nullable=False),
            sa.Column("flow_id", sa.String(64), nullable=False),
            sa.Column("user_id", sa.BigInteger(), nullable=False),
            sa.Column("tenant_id", sa.BigInteger(), nullable=False),
            sa.Column("start_turn_no", sa.Integer(), nullable=False),
            sa.Column("end_turn_no", sa.Integer(), nullable=False),
            sa.Column("depth", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("summary", LargeText, nullable=False),
            sa.Column("token_count", sa.Integer(), nullable=False),
            sa.Column("source_token_count", sa.Integer(), nullable=False),
            sa.Column("source_turn_count", sa.Integer(), nullable=False),
            sa.Column("status", sa.String(16), nullable=False, server_default="building"),
            sa.Column("generator_model", sa.String(128), nullable=True),
            sa.Column("source_digest", sa.String(64), nullable=False),
            sa.Column(
                "create_time",
                sa.DateTime(),
                nullable=False,
                server_default=sa.text("CURRENT_TIMESTAMP"),
            ),
            sa.Column(
                "update_time",
                sa.DateTime(),
                nullable=False,
                server_default=sa.text("CURRENT_TIMESTAMP"),
            ),
            sa.UniqueConstraint(
                "chat_id",
                "depth",
                "start_turn_no",
                "end_turn_no",
                name="uk_chat_turn_summary_range",
            ),
            mysql_charset="utf8mb4",
            mysql_collate="utf8mb4_unicode_ci",
        )
    for index_name, columns in (
        ("ix_chat_turn_summary_chat_id", ["chat_id"]),
        ("ix_chat_turn_summary_flow_id", ["flow_id"]),
        ("ix_chat_turn_summary_user_id", ["user_id"]),
        ("ix_chat_turn_summary_tenant_id", ["tenant_id"]),
        ("ix_chat_turn_summary_status", ["status"]),
        ("idx_chat_turn_summary_range", ["chat_id", "start_turn_no", "end_turn_no"]),
        ("idx_chat_turn_summary_tenant_depth", ["chat_id", "user_id", "tenant_id", "depth"]),
        ("idx_chat_turn_summary_flow_status", ["flow_id", "status"]),
    ):
        if not index_exists(conn, "chat_turn_summary", index_name):
            op.create_index(index_name, "chat_turn_summary", columns)
    if not column_exists(conn, "chat_turn_summary", "context_token_count"):
        op.add_column(
            "chat_turn_summary",
            sa.Column("context_token_count", sa.Integer(), nullable=False, server_default="0"),
        )


def downgrade() -> None:
    conn = op.get_bind()
    if table_exists(conn, "chat_turn_summary"):
        op.drop_table("chat_turn_summary")
    if table_exists(conn, "chat_turn"):
        op.drop_table("chat_turn")
    if index_exists(conn, "message_session", "ix_message_session_active_turn_id"):
        op.drop_index("ix_message_session_active_turn_id", table_name="message_session")
    if column_exists(conn, "message_session", "active_turn_started_at"):
        op.drop_column("message_session", "active_turn_started_at")
    if column_exists(conn, "message_session", "active_turn_id"):
        op.drop_column("message_session", "active_turn_id")
    if index_exists(conn, "chatmessage", "idx_chatmessage_chat_turn_id"):
        op.drop_index("idx_chatmessage_chat_turn_id", table_name="chatmessage")
    if index_exists(conn, "chatmessage", "ix_chatmessage_turn_id"):
        op.drop_index("ix_chatmessage_turn_id", table_name="chatmessage")
    if column_exists(conn, "chatmessage", "turn_id"):
        op.drop_column("chatmessage", "turn_id")
