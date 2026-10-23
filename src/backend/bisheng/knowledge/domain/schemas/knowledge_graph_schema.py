"""Request/response schemas for the knowledge graph API."""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class KnowledgeGraphBuildReq(BaseModel):
    model_id: int | None = Field(default=None, description="抽取模型ID；不传使用知识库统一配置")
    entity_types: list[str] | None = Field(default=None, description="实体类型列表；不传使用统一配置")
    force: bool = Field(default=False, description="图谱已存在时必须为 true 才允许清空重建")


class KnowledgeGraphPublicQueryReq(BaseModel):
    """Anonymous graph query: whole knowledge base, or one entity's 1-hop graph."""

    knowledge_id: int = Field(..., ge=1, description="知识库ID")
    entity_name: str | None = Field(
        default=None,
        max_length=255,
        description="实体名称（精确匹配）；不传或传空则返回知识库完整图谱（最多500个节点）",
    )


class KnowledgeGraphBuildResp(BaseModel):
    job_id: int
    status: int
    total_files: int = 0


class KnowledgeGraphJobInfo(BaseModel):
    job_id: int
    status: int
    total_files: int = 0
    done_files: int = 0
    failed_files: int = 0
    total_chunks: int = 0
    done_chunks: int = 0
    token_in: int = 0
    token_out: int = 0
    error_message: str | None = None


class KnowledgeGraphStatusResp(BaseModel):
    state: int
    graph_built_at: datetime | None = None
    stale: bool = False
    job: KnowledgeGraphJobInfo | None = None


class KnowledgeGraphOverviewResp(BaseModel):
    state: int
    built_at: datetime | None = None
    entity_count: int = 0
    relation_count: int = 0
    stale: bool = False


class KnowledgeGraphNode(BaseModel):
    id: int
    name: str
    type: str
    description: str | None = None
    attributes: dict[str, Any] | None = None
    mention_count: int = 0
    degree: int = 0


class KnowledgeGraphEdge(BaseModel):
    id: int
    source: int
    target: int
    label: str
    weight: float = 1.0
    description: str | None = None


class KnowledgeGraphDataResp(BaseModel):
    nodes: list[KnowledgeGraphNode] = Field(default_factory=list)
    edges: list[KnowledgeGraphEdge] = Field(default_factory=list)
    truncated: bool = False


class KnowledgeGraphEntityItem(BaseModel):
    id: int
    name: str
    type: str
    description: str | None = None
    attributes: dict[str, Any] | None = None
    mention_count: int = 0


class KnowledgeGraphEntityListResp(BaseModel):
    data: list[KnowledgeGraphEntityItem] = Field(default_factory=list)
    total: int = 0


class KnowledgeGraphRelationItem(BaseModel):
    id: int
    direction: str
    label: str
    other_entity: KnowledgeGraphEntityItem
    weight: float = 1.0
    description: str | None = None


class KnowledgeGraphEvidenceItem(BaseModel):
    document_id: int
    document_name: str | None = None
    chunk_index: int
    snippet: str | None = None


class KnowledgeGraphEntityDetailResp(BaseModel):
    entity: KnowledgeGraphEntityItem
    relations: list[KnowledgeGraphRelationItem] = Field(default_factory=list)
    evidence: list[KnowledgeGraphEvidenceItem] = Field(default_factory=list)


class KnowledgeGraphCancelResp(BaseModel):
    job_id: int
    status: int
