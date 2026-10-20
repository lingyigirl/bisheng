"""F078 Agent Card parsing and scheduled preset validation.

The card is a small, whole-read JSON document stored on the registry row:

    {"version": "v1",
     "capabilities": ["chat"],
     "inputs": [{"key": "query", "label": "问题", "type": "string",
                 "required": true, "file": false, "default": ""}]}

It is deliberately not a relational schema: C2 forbids querying JSON columns on
DM8, and nothing filters by capability/input. The card is validated on every
create/update and again when a scheduled task's preset inputs are created or
edited. A card change after tasks exist does not auto-disable them; dispatch
revalidates and fails the run (fail-closed).

File inputs are legal on the card (a third-party agent may accept files) but
**cannot be scheduled** — the scheduler raises 11914 in
``scheduled_task.app_shape_guard``. This module exposes a typed
``AgentCardFileInput`` so that caller can translate it without importing the
scheduler's error module here.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from bisheng.common.errcode.managed_agent import ManagedAgentConfigInvalidError


class AgentCardFileInput(ValueError):
    """Raised when a file-typed input appears in a schedulable context."""


class AgentCardInput(BaseModel):
    model_config = ConfigDict(extra="ignore")

    key: str = Field(min_length=1, max_length=64)
    label: str = Field(default="", max_length=128)
    type: str = Field(default="string")
    required: bool = False
    file: bool = False
    default: Any = None


class AgentCard(BaseModel):
    model_config = ConfigDict(extra="ignore")

    version: str = Field(default="v1", max_length=64)
    capabilities: list[str] = Field(default_factory=list)
    inputs: list[AgentCardInput] = Field(default_factory=list)

    def input_map(self) -> dict[str, AgentCardInput]:
        return {item.key: item for item in self.inputs}


@dataclass(frozen=True)
class NormalizedPreset:
    key: str
    value: str


_SUPPORTED_TYPES = {"string", "number", "integer", "boolean", "object", "array"}


def parse_agent_card(raw: dict | None) -> AgentCard:
    """Parse and validate a card; raises 12102 with the offending field."""
    if raw is None:
        return AgentCard()
    if not isinstance(raw, dict):
        raise ManagedAgentConfigInvalidError(msg="agent_card must be an object")
    try:
        card = AgentCard.model_validate(raw)
    except ValidationError as exc:
        raise ManagedAgentConfigInvalidError(msg=f"invalid agent_card: {exc.errors()[0].get('msg')}") from exc

    seen: set[str] = set()
    for item in card.inputs:
        if item.type not in _SUPPORTED_TYPES:
            raise ManagedAgentConfigInvalidError(msg=f"unsupported input type: {item.type}")
        if item.key in seen:
            raise ManagedAgentConfigInvalidError(msg=f"duplicate input key: {item.key}")
        seen.add(item.key)
    return card


def _coerce(field: AgentCardInput, value: Any) -> Any:
    if value is None:
        return field.default
    try:
        if field.type == "string":
            return str(value)
        if field.type == "integer":
            if isinstance(value, bool):
                raise ValueError
            return int(value)
        if field.type == "number":
            if isinstance(value, bool):
                raise ValueError
            return float(value)
        if field.type == "boolean":
            if isinstance(value, bool):
                return value
            if str(value).lower() in {"true", "1", "yes"}:
                return True
            if str(value).lower() in {"false", "0", "no"}:
                return False
            raise ValueError
        if field.type == "object":
            if not isinstance(value, dict):
                raise ValueError
            return value
        if field.type == "array":
            if not isinstance(value, list):
                raise ValueError
            return value
    except (TypeError, ValueError) as exc:
        raise ManagedAgentConfigInvalidError(msg=f"invalid preset value for {field.key}") from exc
    return value


def validate_preset_inputs(
    card: AgentCard,
    presets: list[dict] | None,
) -> list[NormalizedPreset]:
    """Validate scheduled preset inputs against the card.

    - unknown key -> 12102
    - missing required (no value and no default) -> 12102
    - type mismatch -> 12102
    - file-typed input -> ``AgentCardFileInput`` (caller maps to 11914)
    """
    fields = card.input_map()
    provided = {str(item.get("key")): item.get("value") for item in (presets or [])}
    unknown = [key for key in provided if key not in fields]
    if unknown:
        raise ManagedAgentConfigInvalidError(msg=f"unknown preset input: {unknown[0]}")

    normalized: list[NormalizedPreset] = []
    for key, field in fields.items():
        if field.file:
            raise AgentCardFileInput(key)
        if key not in provided:
            if field.required and field.default is None:
                raise ManagedAgentConfigInvalidError(msg=f"missing required preset input: {key}")
            if field.default is not None:
                normalized.append(NormalizedPreset(key=key, value=str(field.default)))
            continue
        value = _coerce(field, provided[key])
        if value is None and field.required:
            raise ManagedAgentConfigInvalidError(msg=f"missing required preset input: {key}")
        normalized.append(NormalizedPreset(key=key, value=str(value)))
    return normalized
