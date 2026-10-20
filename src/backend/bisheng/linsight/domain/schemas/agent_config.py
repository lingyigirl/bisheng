"""Versioned, credential-free configuration for task-scoped collaborators."""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

MemberId = Annotated[str, StringConstraints(pattern=r"^[a-zA-Z0-9_-]{1,48}$")]
SkillName = Annotated[str, StringConstraints(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$", max_length=64)]
ResourceId = Annotated[int, Field(strict=True, gt=0)]


class SubagentConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    id: MemberId
    name: str = Field(min_length=1, max_length=64)
    description: str = Field(min_length=1, max_length=1000)
    system_prompt: str = Field(default="", max_length=12000)
    model_id: str | None = Field(default=None, pattern=r"^[0-9]+$", max_length=20)
    skill_names: list[SkillName] = Field(default_factory=list, max_length=30)
    tool_refs: list[ResourceId] = Field(default_factory=list, max_length=50)
    connector_refs: list[ResourceId] = Field(default_factory=list, max_length=50)

    @model_validator(mode="after")
    def unique_resources(self):
        tools = [*self.tool_refs, *self.connector_refs]
        if len(tools) != len(set(tools)) or len(self.skill_names) != len(set(self.skill_names)):
            raise ValueError("Resource selections must be unique")
        return self


class AgentConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    subagents: list[SubagentConfig] = Field(default_factory=list, max_length=5)

    @model_validator(mode="after")
    def unique_members(self):
        ids = [member.id for member in self.subagents]
        if len(ids) != len(set(ids)):
            raise ValueError("Collaborator IDs must be unique")
        return self
