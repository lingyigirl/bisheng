"""Request/response schemas for the doc-extract API."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class DocExtractFieldDef(BaseModel):
    key: str = Field(description='字段标识')
    label: str = Field(default='', description='字段中文名')
    type: str = Field(default='string', description='string / number / date / boolean')
    required: bool = Field(default=False)


class DocExtractTemplatePayload(BaseModel):
    name: str
    doc_type: str
    description: str = ''
    field_schema: list[dict[str, Any]] = Field(default_factory=list)
    # An empty render form is legal (spec AC-11).
    render_template: str = ''


class DocExtractEnabledPayload(BaseModel):
    enabled: bool


class DocExtractTrialDefinition(BaseModel):
    """The editor's *unsaved* definition.

    Required for trial extraction (spec M-1b): the server cannot see what is on
    screen, so AC-41 ("run against what is in the editor") is only satisfiable
    if the client sends it.
    """

    description: str = ''
    field_schema: list[dict[str, Any]] = Field(default_factory=list)
    render_template: str = ''
