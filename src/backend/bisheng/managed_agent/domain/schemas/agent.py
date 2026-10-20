"""F078 request/response schemas for the managed-agent management face."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class CredentialReq(BaseModel):
    model_config = ConfigDict(extra="forbid")

    auth_type: str = "none"
    secret: str | None = None
    headers: dict[str, str] | None = None


class ManagedAgentCreateReq(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., min_length=1, max_length=128)
    description: str | None = Field(default=None, max_length=2000)
    source_kind: str
    protocol: str = "native"
    native_app_id: str | None = Field(default=None, max_length=64)
    native_app_type: int | None = None
    endpoint: str | None = Field(default=None, max_length=512)
    model_name: str | None = Field(default=None, max_length=128)
    agent_card: dict | None = None
    credential: CredentialReq | None = None
    health_url: str | None = Field(default=None, max_length=512)
    health_method: str = "test_invoke"
    scenario_code: str | None = Field(default=None, max_length=64)
    industry_code: str | None = Field(default=None, max_length=64)
    tags: list[int] | None = None
    version: str | None = Field(default=None, max_length=64)


class ManagedAgentUpdateReq(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=128)
    description: str | None = Field(default=None, max_length=2000)
    protocol: str | None = None
    native_app_id: str | None = Field(default=None, max_length=64)
    native_app_type: int | None = None
    endpoint: str | None = Field(default=None, max_length=512)
    model_name: str | None = Field(default=None, max_length=128)
    agent_card: dict | None = None
    credential: CredentialReq | None = None
    health_url: str | None = Field(default=None, max_length=512)
    health_method: str | None = None
    scenario_code: str | None = Field(default=None, max_length=64)
    industry_code: str | None = Field(default=None, max_length=64)
    tags: list[int] | None = None
    version: str | None = Field(default=None, max_length=64)


class ManagedAgentItem(BaseModel):
    id: int
    name: str
    description: str | None = None
    source_kind: str
    protocol: str
    status: str
    version: str | None = None
    native_app_id: str | None = None
    native_app_type: int | None = None
    endpoint: str | None = None
    model_name: str | None = None
    scenario_code: str | None = None
    industry_code: str | None = None
    scenario_name: str | None = None
    industry_name: str | None = None
    tags: list[int] = Field(default_factory=list)
    tag_names: list[str] = Field(default_factory=list)
    health_status: str
    health_method: str
    last_health_check_at: datetime | None = None
    last_call_at: datetime | None = None
    last_run_status: str | None = None
    last_latency_ms: int | None = None
    credential_type: str
    has_credential: bool
    credential_mask: str | None = None
    create_time: datetime | None = None
    update_time: datetime | None = None


class TaxonomyCreateReq(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: str
    code: str = Field(..., min_length=1, max_length=64)
    name_zh: str = Field(..., min_length=1, max_length=128)
    name_en: str | None = Field(default=None, max_length=128)
    name_ja: str | None = Field(default=None, max_length=128)
    sort_order: int = 0
    is_enabled: bool = True


class TaxonomyUpdateReq(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str | None = Field(default=None, min_length=1, max_length=64)
    name_zh: str | None = Field(default=None, min_length=1, max_length=128)
    name_en: str | None = Field(default=None, max_length=128)
    name_ja: str | None = Field(default=None, max_length=128)
    sort_order: int | None = None
    is_enabled: bool | None = None


class TaxonomyItem(BaseModel):
    id: int
    kind: str
    code: str
    name_zh: str
    name_en: str | None = None
    name_ja: str | None = None
    sort_order: int
    is_enabled: bool
