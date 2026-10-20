"""Knowledge graph persistence models.

Four tables back the manual knowledge-graph feature:

- ``knowledge_graph_job``: one row per build request, holding progress and cost.
- ``knowledge_graph_entity``: entities extracted from document chunks.
- ``knowledge_graph_relation``: directed relations between entities.
- ``knowledge_graph_evidence``: provenance links from entities/relations back to
  the ES chunk they were extracted from.

The graph is stored in MySQL (no Neo4j dependency) because v1 only serves
browsing/search, not graph retrieval or multi-hop reasoning.
"""

from datetime import datetime
from enum import Enum
from typing import Any

from sqlalchemy import (
    BigInteger,
    Column,
    DateTime,
    Float,
    Index,
    Integer,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlmodel import Field

from bisheng.common.models.base import SQLModelSerializable
from bisheng.core.database.dialect_helpers import UPDATE_TIME_SERVER_DEFAULT, JsonType

DEFAULT_GRAPH_ENTITY_TYPES = ["人物", "组织", "地点", "事件", "产品", "概念"]


class KnowledgeGraphState(int, Enum):
    """Lifecycle state stored on ``knowledge.graph_state``."""

    NOT_GENERATED = 0
    BUILDING = 1
    BUILT = 2
    FAILED = 3
    STALE = 4


class KnowledgeGraphJobStatus(int, Enum):
    """Status of one build job."""

    QUEUED = 0
    RUNNING = 1
    SUCCESS = 2
    PARTIAL_FAILED = 3
    FAILED = 4
    CANCEL_REQUESTED = 5
    CANCELLED = 6


class KnowledgeGraphJob(SQLModelSerializable, table=True):
    """One manual graph build request for a knowledge base."""

    __tablename__ = "knowledge_graph_job"
    __table_args__ = (
        Index("ix_kg_job_tenant", "tenant_id"),
        Index("ix_kg_job_kb_status", "knowledge_id", "status"),
        Index("ix_kg_job_kb_id", "knowledge_id", "id"),
    )

    id: int | None = Field(default=None, sa_column=Column(BigInteger, primary_key=True, autoincrement=True))
    tenant_id: int | None = Field(
        default=None,
        sa_column=Column(Integer, nullable=False, server_default=text("1"), comment="租户ID"),
    )
    knowledge_id: int = Field(sa_column=Column(Integer, nullable=False, comment="知识库ID"))
    status: int = Field(
        default=KnowledgeGraphJobStatus.QUEUED.value,
        sa_column=Column(SmallInteger, nullable=False, server_default=text("0"), comment="0排队 1运行中 2成功 3部分失败 4失败 5取消中 6已取消"),
    )
    model_id: int | None = Field(default=None, sa_column=Column(Integer, nullable=True, comment="抽取使用的LLM模型ID"))
    entity_types: list[str] | None = Field(
        default=None,
        sa_column=Column(JsonType, nullable=True, comment="本次使用的实体类型列表"),
    )
    total_files: int = Field(default=0, sa_column=Column(Integer, nullable=False, server_default=text("0"), comment="待处理文件数"))
    done_files: int = Field(default=0, sa_column=Column(Integer, nullable=False, server_default=text("0"), comment="已完成文件数"))
    failed_files: int = Field(default=0, sa_column=Column(Integer, nullable=False, server_default=text("0"), comment="失败文件数"))
    total_chunks: int = Field(default=0, sa_column=Column(Integer, nullable=False, server_default=text("0"), comment="待处理分块数"))
    done_chunks: int = Field(default=0, sa_column=Column(Integer, nullable=False, server_default=text("0"), comment="已处理分块数"))
    token_in: int = Field(default=0, sa_column=Column(BigInteger, nullable=False, server_default=text("0"), comment="输入token数"))
    token_out: int = Field(default=0, sa_column=Column(BigInteger, nullable=False, server_default=text("0"), comment="输出token数"))
    error_message: str | None = Field(
        default=None,
        sa_column=Column(String(1000), nullable=True, comment="失败原因"),
    )
    create_user_id: int | None = Field(default=None, sa_column=Column(Integer, nullable=True, comment="发起人ID"))
    create_time: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime, nullable=False, server_default=text("CURRENT_TIMESTAMP"), comment="创建时间"),
    )
    start_time: datetime | None = Field(default=None, sa_column=Column(DateTime, nullable=True, comment="开始时间"))
    end_time: datetime | None = Field(default=None, sa_column=Column(DateTime, nullable=True, comment="结束时间"))
    update_time: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime, nullable=False, server_default=UPDATE_TIME_SERVER_DEFAULT, comment="更新时间"),
    )


class KnowledgeGraphEntity(SQLModelSerializable, table=True):
    """A canonical entity within one knowledge base."""

    __tablename__ = "knowledge_graph_entity"
    __table_args__ = (
        UniqueConstraint("knowledge_id", "normalized_name", "entity_type", name="uk_kg_entity"),
        Index("ix_kg_entity_type", "knowledge_id", "entity_type"),
        Index("ix_kg_entity_mention", "knowledge_id", "mention_count"),
        Index("ix_kg_entity_name", "knowledge_id", "name"),
    )

    id: int | None = Field(default=None, sa_column=Column(BigInteger, primary_key=True, autoincrement=True))
    tenant_id: int | None = Field(
        default=None,
        sa_column=Column(Integer, nullable=False, server_default=text("1"), comment="租户ID"),
    )
    knowledge_id: int = Field(sa_column=Column(Integer, nullable=False, comment="知识库ID"))
    name: str = Field(sa_column=Column(String(255), nullable=False, comment="实体名称（原文）"))
    normalized_name: str = Field(sa_column=Column(String(255), nullable=False, comment="归一化名称，用于实体合并"))
    entity_type: str = Field(sa_column=Column(String(64), nullable=False, comment="实体类型"))
    description: str | None = Field(default=None, sa_column=Column(Text, nullable=True, comment="实体描述"))
    attributes: dict[str, Any] | None = Field(
        default=None,
        sa_column=Column(JsonType, nullable=True, comment="属性键值对"),
    )
    aliases: list[str] | None = Field(
        default=None,
        sa_column=Column(JsonType, nullable=True, comment="别名列表"),
    )
    mention_count: int = Field(
        default=1,
        sa_column=Column(Integer, nullable=False, server_default=text("1"), comment="提及次数，用于Top-N排序"),
    )
    create_time: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime, nullable=False, server_default=text("CURRENT_TIMESTAMP"), comment="创建时间"),
    )
    update_time: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime, nullable=False, server_default=UPDATE_TIME_SERVER_DEFAULT, comment="更新时间"),
    )


class KnowledgeGraphRelation(SQLModelSerializable, table=True):
    """A directed relation between two entities in one knowledge base."""

    __tablename__ = "knowledge_graph_relation"
    __table_args__ = (
        UniqueConstraint(
            "knowledge_id", "source_entity_id", "target_entity_id", "rel_type", name="uk_kg_relation"
        ),
        Index("ix_kg_rel_source", "knowledge_id", "source_entity_id"),
        Index("ix_kg_rel_target", "knowledge_id", "target_entity_id"),
    )

    id: int | None = Field(default=None, sa_column=Column(BigInteger, primary_key=True, autoincrement=True))
    tenant_id: int | None = Field(
        default=None,
        sa_column=Column(Integer, nullable=False, server_default=text("1"), comment="租户ID"),
    )
    knowledge_id: int = Field(sa_column=Column(Integer, nullable=False, comment="知识库ID"))
    source_entity_id: int = Field(sa_column=Column(BigInteger, nullable=False, comment="源实体ID"))
    target_entity_id: int = Field(sa_column=Column(BigInteger, nullable=False, comment="目标实体ID"))
    rel_type: str = Field(sa_column=Column(String(64), nullable=False, comment="关系类型"))
    description: str | None = Field(default=None, sa_column=Column(Text, nullable=True, comment="关系描述"))
    weight: float = Field(
        default=1.0,
        sa_column=Column(Float, nullable=False, server_default=text("1"), comment="关系置信度 0~1"),
    )
    attributes: dict[str, Any] | None = Field(
        default=None,
        sa_column=Column(JsonType, nullable=True, comment="关系属性"),
    )
    mention_count: int = Field(
        default=1,
        sa_column=Column(Integer, nullable=False, server_default=text("1"), comment="提及次数"),
    )
    create_time: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime, nullable=False, server_default=text("CURRENT_TIMESTAMP"), comment="创建时间"),
    )
    update_time: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime, nullable=False, server_default=UPDATE_TIME_SERVER_DEFAULT, comment="更新时间"),
    )


class KnowledgeGraphEvidence(SQLModelSerializable, table=True):
    """Provenance link from an entity/relation to an ES chunk.

    Exactly one of ``entity_id`` / ``relation_id`` is set. Enforced in the
    service layer rather than by a DB CHECK constraint to stay portable across
    MySQL and DM8.
    """

    __tablename__ = "knowledge_graph_evidence"
    __table_args__ = (
        Index("ix_kg_ev_doc", "document_id"),
        Index("ix_kg_ev_entity", "entity_id"),
        Index("ix_kg_ev_relation", "relation_id"),
        Index("ix_kg_ev_kb", "knowledge_id"),
    )

    id: int | None = Field(default=None, sa_column=Column(BigInteger, primary_key=True, autoincrement=True))
    tenant_id: int | None = Field(
        default=None,
        sa_column=Column(Integer, nullable=False, server_default=text("1"), comment="租户ID"),
    )
    knowledge_id: int = Field(sa_column=Column(Integer, nullable=False, comment="知识库ID"))
    entity_id: int | None = Field(default=None, sa_column=Column(BigInteger, nullable=True, comment="实体ID；与relation_id二选一"))
    relation_id: int | None = Field(
        default=None, sa_column=Column(BigInteger, nullable=True, comment="关系ID；与entity_id二选一")
    )
    document_id: int = Field(sa_column=Column(BigInteger, nullable=False, comment="知识库文件ID（knowledgefile.id）"))
    chunk_index: int = Field(sa_column=Column(Integer, nullable=False, comment="分块序号"))
    chunk_pk: str | None = Field(
        default=None,
        sa_column=Column(String(128), nullable=True, comment="ES文档_id，便于精确回查"),
    )
    confidence: float | None = Field(default=None, sa_column=Column(Float, nullable=True, comment="抽取置信度"))
    create_time: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime, nullable=False, server_default=text("CURRENT_TIMESTAMP"), comment="创建时间"),
    )
