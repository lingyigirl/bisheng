import re
from datetime import datetime
from typing import Optional, List, Any

from pydantic import BaseModel, Field, field_validator

from bisheng.knowledge.domain.models.knowledge import MetadataFieldType


class UpdatePreviewFileChunk(BaseModel):
    knowledge_id: int = Field(..., description="The knowledge base uponID")
    file_path: str = Field(..., description="FilePath")
    text: str = Field(..., description="Text block Content")
    chunk_index: int = Field(..., description="Text block index, Insidemetadatamile")
    bbox: Optional[str] = Field(default="", description="Text blocksbboxMessage")


class MetadataField(BaseModel):
    """Metadata Field Model"""

    field_name: str = Field(..., max_length=255, description="Metadata field name")
    field_type: MetadataFieldType = Field(..., description="Metadata field type")
    updated_at: int = Field(
        default_factory=lambda: int(datetime.now().timestamp()), description="Metadata field update timestamp"
    )

    @field_validator("field_name")
    @classmethod
    def validate_field_name(cls, v: str) -> str:
        # Must consist of lowercase letters, numbers, underscores, and must begin with a lowercase letter
        pattern = r"^[a-z][a-z0-9_]*$"
        if not re.match(pattern, v):
            raise ValueError(
                "field_name must start with a lowercase letter and contain only lowercase letters, numbers, and underscores, current value: {v}"
            )
        return v


class AddKnowledgeMetadataFieldsReq(BaseModel):
    """Add Knowledge Base Metadata Field Request Body"""

    knowledge_id: int = Field(..., description="The knowledge base uponID")
    metadata_fields: List[MetadataField] = Field(..., description="List of metadata fields to add")


class UpdateKnowledgeMetadataFieldsReq(BaseModel):
    """Update Knowledge Base Metadata Field Request Body"""

    class UpdateMetadataFieldName(BaseModel):
        """Update Metadata Field Name Model"""

        old_field_name: str = Field(..., description="Old Metadata Field Name")
        new_field_name: str = Field(..., max_length=255, description="New Metadata Field Name")

        @field_validator("new_field_name")
        @classmethod
        def validate_new_field_name(cls, v: str) -> str:
            # Must consist of lowercase letters, numbers, underscores, and must begin with a lowercase letter
            pattern = r"^[a-z][a-z0-9_]*$"
            if not re.match(pattern, v):
                raise ValueError(
                    f"new_field_name must start with a lowercase letter and contain only lowercase letters, numbers, and underscores, current value: {v}"
                )
            return v

        @field_validator("old_field_name")
        @classmethod
        def validate_old_field_name(cls, v: str) -> str:
            # Tidak boleh kosong.
            if not v:
                raise ValueError("old_field_name cannot be empty")
            return v

    knowledge_id: int = Field(..., description="The knowledge base uponID")
    metadata_fields: List[UpdateMetadataFieldName] = Field(..., description="List of metadata fields to update")


class FileUserMetaDataInfo(BaseModel):
    field_name: str = Field(..., description="Metadata field name")
    field_value: Optional[Any] = Field(default=None, description="Metadata field value")
    updated_at: int = Field(
        default_factory=lambda: int(datetime.now().timestamp()), description="Metadata field update timestamp"
    )


class ModifyKnowledgeFileMetaDataReq(BaseModel):
    """Change Knowledge Base File Metadata Request Body"""

    knowledge_file_id: int = Field(..., description="Knowledge Base FilesID")
    user_metadata_list: List[FileUserMetaDataInfo] = Field(..., description="List of file metadata to add")


class ModifyFileEffectivePeriodReq(BaseModel):
    """F055: set file-level effective period (null = unlimited, spec AC-06)."""

    knowledge_id: int = Field(..., description="知识库ID")
    file_id: int = Field(..., description="文件ID")
    effective_date: Optional[datetime] = Field(default=None, description="生效日期, null=不限")
    expire_date: Optional[datetime] = Field(default=None, description="失效日期, null=不限")


class UpdateFileTagsReq(BaseModel):
    knowledge_id: int = Field(..., description="知识库ID")
    file_id: int = Field(..., description="文件ID")
    tag_ids: List[int] = Field(..., description="全量标签 ID，最多 5 个")


class BatchAddFileTagsReq(BaseModel):
    knowledge_id: int = Field(..., description="知识库ID")
    file_ids: List[int] = Field(..., description="文件ID列表")
    tag_ids: List[int] = Field(..., description="追加的标签 ID 列表")


class HitTestReq(BaseModel):
    """F055 (AC-14): hit-test request — a question tested against the KB's real retrieval."""

    question: str = Field(..., min_length=1, description="测试问句")


class HitTestChunk(BaseModel):
    """F055 (AC-14): one recalled chunk in the hit-test response."""

    document_id: int = Field(default=0, description="文档ID")
    document_name: Optional[str] = Field(default=None, description="文档名")
    chunk: str = Field(default="", description="命中片段")
    chunk_index: Optional[int] = Field(default=None, description="片段序号")
    score: float = Field(default=0.0, description="相关度分数(RRF融合)")
    knowledge_id: int = Field(default=0, description="知识库ID")


class HitTestExcluded(BaseModel):
    """F055 (AC-14): files excluded by the effective-period filter (stats only)."""

    expired: int = Field(default=0, description="已失效被排除的文件数")
    not_yet_effective: int = Field(default=0, description="尚未生效被排除的文件数")


class HitTestResp(BaseModel):
    """F055 (AC-14): hit-test response — hits + effective-period exclusion stats."""

    hits: List[HitTestChunk] = Field(default_factory=list, description="命中列表")
    excluded: HitTestExcluded = Field(default_factory=HitTestExcluded, description="时效排除统计")
