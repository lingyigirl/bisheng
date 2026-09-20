from enum import Enum
from typing import Literal, Union

from pydantic import BaseModel, ConfigDict, Field

from bisheng.datasource.domain.schemas.citation import DatasourceCitationPayloadSchema


class CitationSchemaBase(BaseModel):
    """Base schema for citation protocol models."""

    model_config = ConfigDict(
        validate_by_alias=True,
        validate_by_name=True,
        arbitrary_types_allowed=True,
    )


class CitationType(str, Enum):
    """Supported citation source types."""

    RAG = "rag"
    WEB = "web"
    # F061: an answer produced by querying a datasource. Carries the tables and
    # the statement that ran, so it is subject to the same authorization
    # question as RAG -- see AUTHORIZED_TYPES below.
    DATASOURCE = "datasource"


#: Types whose payload names a platform resource and must therefore be filtered
#: against the viewer's permissions before being served.
#:
#: Exists so the checks below stop asking "is this RAG?" and start asking the
#: question they actually mean. When a new type landed with the old form, the
#: `else` branch treated it as WEB -- i.e. as anonymous content to pass through
#: unfiltered. For a datasource citation, which carries table names and SQL,
#: that would have shown an unauthorized viewer another tenant's schema.
AUTHORIZED_TYPES = frozenset({CitationType.RAG, CitationType.DATASOURCE})


class RagCitationItemSchema(CitationSchemaBase):
    """Chunk-level payload inside a grouped RAG citation item."""

    itemId: str = Field(..., description="Stable item identifier inside the citation")
    chunkId: str | None = Field(default=None, description="Chunk identifier")
    chunkIndex: int | None = Field(default=None, description="Chunk index inside the file")
    content: str | None = Field(default=None, description="Chunk content")
    bbox: str | None = Field(default=None, description="Bounding box information")
    page: int | None = Field(default=None, description="Page number")


class RagCitationPayloadSchema(CitationSchemaBase):
    """Payload for knowledge-based citation items."""

    knowledgeId: int | None = Field(default=None, description="Knowledge space identifier")
    knowledgeName: str | None = Field(default=None, description="Knowledge space display name")
    fileType: str | None = Field(default=None, description="Knowledge file type")
    documentId: int | None = Field(default=None, description="Document identifier")
    documentName: str | None = Field(default=None, description="Document display name")
    snippet: str | None = Field(default=None, description="Matched chunk text snippet")
    previewUrl: str | None = Field(default=None, description="Document preview URL")
    downloadUrl: str | None = Field(default=None, description="Document download URL")
    sourceUrl: str | None = Field(default=None, description="Original source URL if available")
    items: list[RagCitationItemSchema] = Field(
        default_factory=list,
        description="Grouped chunk items for the same file",
    )


class WebCitationItemSchema(CitationSchemaBase):
    """Snippet-level payload inside a grouped web citation item."""

    itemId: str = Field(..., description="Stable item identifier inside the citation")
    snippet: str | None = Field(default=None, description="Matched snippet content")
    title: str | None = Field(default=None, description="Snippet title override if needed")


class WebCitationPayloadSchema(CitationSchemaBase):
    """Payload for web search citation items."""

    url: str = Field(..., description="Canonical web page URL")
    title: str | None = Field(default=None, description="Web page title")
    snippet: str | None = Field(default=None, description="Search result snippet")
    source: str | None = Field(default=None, description="Search result source name")
    siteIcon: str | None = Field(default=None, description="Site icon URL")
    datePublished: str | None = Field(default=None, description="Published time in ISO 8601 string")
    items: list[WebCitationItemSchema] = Field(
        default_factory=list,
        description="Grouped snippet items for the same page",
    )


# F061: a datasource-backed answer carries its own payload shape. Added to the
# union rather than widened to `dict`, so every existing `model_validate` call
# keeps validating -- a permissive union would let a malformed payload reach the
# frontend unchecked.
CitationSourcePayload = Union[
    RagCitationPayloadSchema, WebCitationPayloadSchema, DatasourceCitationPayloadSchema
]


class CitationRegistryItemSchema(CitationSchemaBase):
    """Normalized citation registry item shared by all chains."""

    key: str | None = Field(default=None, description="Flattened citation item key when item-level view is needed")
    citationId: str = Field(..., description="Unique citation identifier")
    type: CitationType = Field(..., description="Citation source type")
    itemId: str | None = Field(default=None, description="Item identifier inside the parent citation")
    sourcePayload: CitationSourcePayload = Field(..., description="Structured source payload")
    accessScope: Literal["per_user", "shared"] = Field(
        default="per_user",
        description=(
            "F041 retrieval access gate. 'per_user': resolve filters the citation by the viewing "
            "user's view_file (F029 strict, dropped when unauthorized). 'shared': config-author-scoped "
            "(toggle OFF) — resolve keeps source metadata but still gates full-file preview/download "
            "URLs by the viewer view_file."
        ),
    )


class ResolveCitationRequest(CitationSchemaBase):
    """Batch resolve request for citation items."""

    citationIds: list[str] = Field(default_factory=list, description="Citation identifiers to resolve")


class ResolveCitationResponse(CitationSchemaBase):
    """Batch resolve response for citation items."""

    items: list[CitationRegistryItemSchema] = Field(default_factory=list, description="Resolved citation items")


class CitationRegistrySSEPayload(CitationSchemaBase):
    """SSE payload for registry emission."""

    messageId: str | None = Field(default=None, description="Message identifier for citation registry")
    items: list[CitationRegistryItemSchema] = Field(default_factory=list, description="Citation registry items")
