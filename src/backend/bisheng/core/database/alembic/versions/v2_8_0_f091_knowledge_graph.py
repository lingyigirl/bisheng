"""Knowledge graph tables and knowledge.graph_* columns.

Guarded so it converges whether the tables/columns were created by
``create_all`` on a fresh install, by the manual ``schema.sql`` script, or by
this migration on an existing deployment.

Revision ID: f091_knowledge_graph
Revises: f035_skill_object_storage
Create Date: 2026-10-20
"""

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op

from bisheng.core.database.dialect_helpers import (
    JsonType,
    column_exists,
    index_exists,
    table_exists,
    update_time_server_default,
)

revision: str = "f091_knowledge_graph"
down_revision: Union[str, Sequence[str], None] = "f035_skill_object_storage"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_JOB_TABLE = "knowledge_graph_job"
_ENTITY_TABLE = "knowledge_graph_entity"
_RELATION_TABLE = "knowledge_graph_relation"
_EVIDENCE_TABLE = "knowledge_graph_evidence"


def _create_index(conn, table_name: str, index_name: str, columns: list[str]) -> None:
    if not index_exists(conn, table_name, index_name):
        op.create_index(index_name, table_name, columns)


def upgrade() -> None:
    conn = op.get_bind()

    # 1. knowledge.graph_* columns
    if not column_exists(conn, "knowledge", "graph_state"):
        op.add_column(
            "knowledge",
            sa.Column(
                "graph_state",
                sa.SmallInteger(),
                nullable=False,
                server_default="0",
                comment="知识图谱状态 0未生成 1生成中 2已生成 3生成失败 4已过期",
            ),
        )
    if not column_exists(conn, "knowledge", "graph_built_at"):
        op.add_column(
            "knowledge",
            sa.Column(
                "graph_built_at",
                sa.DateTime(),
                nullable=True,
                comment="最近一次知识图谱成功生成时间",
            ),
        )
    if not column_exists(conn, "knowledge", "graph_job_id"):
        op.add_column(
            "knowledge",
            sa.Column(
                "graph_job_id",
                sa.BigInteger(),
                nullable=True,
                comment="最近一次知识图谱生成任务ID",
            ),
        )

    # 2. knowledge_graph_job
    if not table_exists(conn, _JOB_TABLE):
        op.create_table(
            _JOB_TABLE,
            sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True, comment="主键"),
            sa.Column("tenant_id", sa.Integer(), nullable=False, server_default="1", comment="租户ID"),
            sa.Column("knowledge_id", sa.Integer(), nullable=False, comment="知识库ID"),
            sa.Column(
                "status",
                sa.SmallInteger(),
                nullable=False,
                server_default="0",
                comment="0排队 1运行中 2成功 3部分失败 4失败 5取消中 6已取消",
            ),
            sa.Column("model_id", sa.Integer(), nullable=True, comment="抽取使用的LLM模型ID"),
            sa.Column("entity_types", JsonType, nullable=True, comment="本次使用的实体类型列表"),
            sa.Column("total_files", sa.Integer(), nullable=False, server_default="0", comment="待处理文件数"),
            sa.Column("done_files", sa.Integer(), nullable=False, server_default="0", comment="已完成文件数"),
            sa.Column("failed_files", sa.Integer(), nullable=False, server_default="0", comment="失败文件数"),
            sa.Column("total_chunks", sa.Integer(), nullable=False, server_default="0", comment="待处理分块数"),
            sa.Column("done_chunks", sa.Integer(), nullable=False, server_default="0", comment="已处理分块数"),
            sa.Column("token_in", sa.BigInteger(), nullable=False, server_default="0", comment="输入token数"),
            sa.Column("token_out", sa.BigInteger(), nullable=False, server_default="0", comment="输出token数"),
            sa.Column("error_message", sa.String(1000), nullable=True, comment="失败原因"),
            sa.Column("create_user_id", sa.Integer(), nullable=True, comment="发起人ID"),
            sa.Column(
                "create_time",
                sa.DateTime(),
                nullable=False,
                server_default=sa.text("CURRENT_TIMESTAMP"),
                comment="创建时间",
            ),
            sa.Column("start_time", sa.DateTime(), nullable=True, comment="开始时间"),
            sa.Column("end_time", sa.DateTime(), nullable=True, comment="结束时间"),
            sa.Column(
                "update_time",
                sa.DateTime(),
                nullable=False,
                server_default=update_time_server_default(conn),
                comment="更新时间",
            ),
            comment="知识图谱生成任务",
        )
    _create_index(conn, _JOB_TABLE, "ix_kg_job_tenant", ["tenant_id"])
    _create_index(conn, _JOB_TABLE, "ix_kg_job_kb_status", ["knowledge_id", "status"])
    _create_index(conn, _JOB_TABLE, "ix_kg_job_kb_id", ["knowledge_id", "id"])

    # 3. knowledge_graph_entity
    if not table_exists(conn, _ENTITY_TABLE):
        op.create_table(
            _ENTITY_TABLE,
            sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True, comment="主键"),
            sa.Column("tenant_id", sa.Integer(), nullable=False, server_default="1", comment="租户ID"),
            sa.Column("knowledge_id", sa.Integer(), nullable=False, comment="知识库ID"),
            sa.Column("name", sa.String(255), nullable=False, comment="实体名称（原文）"),
            sa.Column("normalized_name", sa.String(255), nullable=False, comment="归一化名称，用于实体合并"),
            sa.Column("entity_type", sa.String(64), nullable=False, comment="实体类型"),
            sa.Column("description", sa.Text(), nullable=True, comment="实体描述"),
            sa.Column("attributes", JsonType, nullable=True, comment="属性键值对"),
            sa.Column("aliases", JsonType, nullable=True, comment="别名列表"),
            sa.Column("mention_count", sa.Integer(), nullable=False, server_default="1", comment="提及次数，用于Top-N排序"),
            sa.Column(
                "create_time",
                sa.DateTime(),
                nullable=False,
                server_default=sa.text("CURRENT_TIMESTAMP"),
                comment="创建时间",
            ),
            sa.Column(
                "update_time",
                sa.DateTime(),
                nullable=False,
                server_default=update_time_server_default(conn),
                comment="更新时间",
            ),
            sa.UniqueConstraint("knowledge_id", "normalized_name", "entity_type", name="uk_kg_entity"),
            comment="知识图谱实体",
        )
    _create_index(conn, _ENTITY_TABLE, "ix_kg_entity_type", ["knowledge_id", "entity_type"])
    _create_index(conn, _ENTITY_TABLE, "ix_kg_entity_mention", ["knowledge_id", "mention_count"])
    _create_index(conn, _ENTITY_TABLE, "ix_kg_entity_name", ["knowledge_id", "name"])

    # 4. knowledge_graph_relation
    if not table_exists(conn, _RELATION_TABLE):
        op.create_table(
            _RELATION_TABLE,
            sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True, comment="主键"),
            sa.Column("tenant_id", sa.Integer(), nullable=False, server_default="1", comment="租户ID"),
            sa.Column("knowledge_id", sa.Integer(), nullable=False, comment="知识库ID"),
            sa.Column("source_entity_id", sa.BigInteger(), nullable=False, comment="源实体ID"),
            sa.Column("target_entity_id", sa.BigInteger(), nullable=False, comment="目标实体ID"),
            sa.Column("rel_type", sa.String(64), nullable=False, comment="关系类型"),
            sa.Column("description", sa.Text(), nullable=True, comment="关系描述"),
            sa.Column("weight", sa.Float(), nullable=False, server_default="1", comment="关系置信度 0~1"),
            sa.Column("attributes", JsonType, nullable=True, comment="关系属性"),
            sa.Column("mention_count", sa.Integer(), nullable=False, server_default="1", comment="提及次数"),
            sa.Column(
                "create_time",
                sa.DateTime(),
                nullable=False,
                server_default=sa.text("CURRENT_TIMESTAMP"),
                comment="创建时间",
            ),
            sa.Column(
                "update_time",
                sa.DateTime(),
                nullable=False,
                server_default=update_time_server_default(conn),
                comment="更新时间",
            ),
            sa.UniqueConstraint(
                "knowledge_id", "source_entity_id", "target_entity_id", "rel_type", name="uk_kg_relation"
            ),
            comment="知识图谱关系",
        )
    _create_index(conn, _RELATION_TABLE, "ix_kg_rel_source", ["knowledge_id", "source_entity_id"])
    _create_index(conn, _RELATION_TABLE, "ix_kg_rel_target", ["knowledge_id", "target_entity_id"])

    # 5. knowledge_graph_evidence
    if not table_exists(conn, _EVIDENCE_TABLE):
        op.create_table(
            _EVIDENCE_TABLE,
            sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True, comment="主键"),
            sa.Column("tenant_id", sa.Integer(), nullable=False, server_default="1", comment="租户ID"),
            sa.Column("knowledge_id", sa.Integer(), nullable=False, comment="知识库ID"),
            sa.Column("entity_id", sa.BigInteger(), nullable=True, comment="实体ID；与relation_id二选一"),
            sa.Column("relation_id", sa.BigInteger(), nullable=True, comment="关系ID；与entity_id二选一"),
            sa.Column("document_id", sa.BigInteger(), nullable=False, comment="知识库文件ID（knowledgefile.id）"),
            sa.Column("chunk_index", sa.Integer(), nullable=False, comment="分块序号"),
            sa.Column("chunk_pk", sa.String(128), nullable=True, comment="ES文档_id，便于精确回查"),
            sa.Column("confidence", sa.Float(), nullable=True, comment="抽取置信度"),
            sa.Column(
                "create_time",
                sa.DateTime(),
                nullable=False,
                server_default=sa.text("CURRENT_TIMESTAMP"),
                comment="创建时间",
            ),
            comment="知识图谱证据",
        )
    _create_index(conn, _EVIDENCE_TABLE, "ix_kg_ev_doc", ["document_id"])
    _create_index(conn, _EVIDENCE_TABLE, "ix_kg_ev_entity", ["entity_id"])
    _create_index(conn, _EVIDENCE_TABLE, "ix_kg_ev_relation", ["relation_id"])
    _create_index(conn, _EVIDENCE_TABLE, "ix_kg_ev_kb", ["knowledge_id"])


def downgrade() -> None:
    """Drop graph tables and the knowledge.graph_* columns (data loss)."""
    conn = op.get_bind()

    for table_name, indexes in (
        (_EVIDENCE_TABLE, ["ix_kg_ev_doc", "ix_kg_ev_entity", "ix_kg_ev_relation", "ix_kg_ev_kb"]),
        (_RELATION_TABLE, ["ix_kg_rel_source", "ix_kg_rel_target"]),
        (_ENTITY_TABLE, ["ix_kg_entity_type", "ix_kg_entity_mention", "ix_kg_entity_name"]),
        (_JOB_TABLE, ["ix_kg_job_tenant", "ix_kg_job_kb_status", "ix_kg_job_kb_id"]),
    ):
        if table_exists(conn, table_name):
            for index_name in indexes:
                if index_exists(conn, table_name, index_name):
                    op.drop_index(index_name, table_name=table_name)
            op.drop_table(table_name)

    if column_exists(conn, "knowledge", "graph_job_id"):
        op.drop_column("knowledge", "graph_job_id")
    if column_exists(conn, "knowledge", "graph_built_at"):
        op.drop_column("knowledge", "graph_built_at")
    if column_exists(conn, "knowledge", "graph_state"):
        op.drop_column("knowledge", "graph_state")
