from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator


class SensitiveWordBusinessType(str, Enum):
    KNOWLEDGE_SPACE_FILE_PARSE = 'knowledge_space_file_parse'
    CHANNEL_ARTICLE = 'channel_article'
    CHAT_INPUT = 'chat_input'
    CHAT_OUTPUT = 'chat_output'


class SensitiveWordScopeType(str, Enum):
    TENANT = 'tenant'


SensitiveWordType = Literal['builtin', 'custom']


class SensitiveWordPolicyPayload(BaseModel):
    enabled: bool = False
    words_types: list[SensitiveWordType] = Field(default_factory=list)
    custom_words: str = ''
    auto_reply: str = ''
    extra_config: dict[str, Any] = Field(default_factory=dict)

    @field_validator('words_types')
    @classmethod
    def dedupe_words_types(cls, value: list[str]) -> list[str]:
        allowed = {'builtin', 'custom'}
        result: list[str] = []
        for item in value or []:
            if item in allowed and item not in result:
                result.append(item)
        return result


class SensitiveWordPolicyResp(SensitiveWordPolicyPayload):
    tenant_id: int
    business_type: SensitiveWordBusinessType
    scope_type: SensitiveWordScopeType = SensitiveWordScopeType.TENANT
    scope_id: str


class SensitiveWordHit(BaseModel):
    word: str
    count: int


class SensitiveWordCheckResult(BaseModel):
    enabled: bool = False
    hits: list[SensitiveWordHit] = Field(default_factory=list)
    auto_reply: str | None = None


class SensitiveWordHitLogItem(BaseModel):
    """One hit-log row for the platform log page (summary only, never raw text)."""

    id: int
    business_type: str
    direction: str
    flow_id: str | None = None
    chat_id: str | None = None
    user_id: int | None = None
    hits: list[SensitiveWordHit] = Field(default_factory=list)
    action: str
    # F059: which layer produced the hit. `tenant` for every pre-F059 row and for
    # rows written by the tenant policy; `platform` for the mandatory platform
    # library. A NULL from a rolling deploy reports as `tenant`, matching the
    # column's server default.
    source: str = 'tenant'
    # Super-admin view only; the tenant view is already scoped to one tenant,
    # where repeating the id on every row is noise.
    tenant_id: int | None = None
    # Number of distinct hit words. Separate from `hits` because the tenant view
    # strips `hits` for platform rows but still shows how many matched — the
    # tenant learns that a block happened and its extent, without learning the
    # words (spec AC-18).
    hit_count: int = 0
    create_time: datetime
