"""Request/response shapes for the saved-collaborator library (F087)."""

from pydantic import BaseModel, ConfigDict, Field

from bisheng.linsight.domain.schemas.agent_config import ResourceId, SkillName


class MemberSaveRequest(BaseModel):
    """One collaborator template to save (upsert by name for the current user).

    Field limits mirror ``SubagentConfig`` so a stored template can always be
    turned back into a task-time member without a second validation surface.
    ``id`` set -> update that owned row in place (rename included); absent ->
    create, or overwrite the row with the same name.
    """

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    id: int | None = Field(default=None, ge=1)
    name: str = Field(min_length=1, max_length=64)
    description: str = Field(min_length=1, max_length=1000)
    system_prompt: str = Field(default="", max_length=12000)
    model_id: str | None = Field(default=None, pattern=r"^[0-9]+$", max_length=20)
    skill_names: list[SkillName] = Field(default_factory=list, max_length=30)
    tool_refs: list[ResourceId] = Field(default_factory=list, max_length=50)
    connector_refs: list[ResourceId] = Field(default_factory=list, max_length=50)


class MemberDefaultRequest(BaseModel):
    """Toggle the auto-carry flag of one saved member."""

    model_config = ConfigDict(extra="forbid")

    is_default: bool
