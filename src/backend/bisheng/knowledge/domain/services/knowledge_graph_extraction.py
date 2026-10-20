"""LLM-side entity/relation extraction for the knowledge graph feature.

Kept separate from the orchestration service so the parsing/normalisation rules
can be unit-tested without a database, Redis or Celery.
"""

from __future__ import annotations

import json
import re
import unicodedata
from typing import Any

from json_repair import json_repair
from langchain_core.messages import HumanMessage, SystemMessage
from loguru import logger

from bisheng.common.constants.enums.telemetry import ApplicationTypeEnum
from bisheng.core.prompts.manager import get_prompt_manager_sync
from bisheng.llm.domain import LLMService

PROMPT_NAMESPACE = "knowledge_graph"
PROMPT_NAME = "extract_prompt"

MAX_ENTITIES_PER_CHUNK = 15
MAX_RELATIONS_PER_CHUNK = 20
MAX_RETRIES = 2


def normalize_entity_name(name: str) -> str:
    """Normalise an entity name for deterministic merging.

    Full/half-width differences, whitespace, punctuation and case are the
    common duplicate sources in Chinese documents. We intentionally do NOT do
    semantic alias resolution here — that is a later enhancement.
    """
    name = unicodedata.normalize("NFKC", name or "").strip().lower()
    name = re.sub(r"\s+", "", name)
    name = name.strip("，。；：、,.!?()（）[]【】\"'“”‘’")
    return name[:255]


def _content_to_text(content: Any) -> str:
    """Flatten LangChain message content (str or block list) to text."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                text = item.get("text")
                if text:
                    parts.append(str(text))
        return "".join(parts)
    return str(content or "")


def _json_loads(text: str) -> Any:
    """Parse a model response, tolerating a Markdown fence and minor JSON faults."""
    text = (text or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
        text = re.sub(r"\s*```$", "", text)
    try:
        return json.loads(text)
    except (TypeError, ValueError):
        return json_repair.loads(text)


def _usage_from_response(response: Any, prompt_text: str, output_text: str) -> tuple[int, int]:
    """Read token usage when the provider reports it; otherwise estimate."""
    usage = getattr(response, "usage_metadata", None) or {}
    token_in = usage.get("input_tokens") or usage.get("prompt_tokens") or 0
    token_out = usage.get("output_tokens") or usage.get("completion_tokens") or 0
    if not token_in or not token_out:
        metadata = getattr(response, "response_metadata", None) or {}
        raw = metadata.get("token_usage") or metadata.get("usage") or {}
        if isinstance(raw, dict):
            token_in = token_in or raw.get("prompt_tokens") or raw.get("input_tokens") or 0
            token_out = token_out or raw.get("completion_tokens") or raw.get("output_tokens") or 0
    # Rough fallback; only used for UI cost display, never for billing.
    token_in = int(token_in or max(1, len(prompt_text) // 4))
    token_out = int(token_out or max(1, len(output_text) // 4))
    return token_in, token_out


def _coerce_attributes(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    result: dict[str, Any] = {}
    for key, item in value.items():
        if key is None:
            continue
        key_text = str(key).strip()
        if not key_text:
            continue
        if isinstance(item, (dict, list)):
            result[key_text] = item
        elif item is not None:
            result[key_text] = str(item)
    return result or None


def validate_extraction_payload(
    payload: Any,
    allowed_entity_types: list[str],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Validate and normalise one LLM response.

    Returns ``(entities, relations)``. Relations whose endpoints are not in the
    accepted entity list are dropped — a relation to a hallucinated node is
    worse than a missing relation.
    """
    if not isinstance(payload, dict):
        return [], []

    allowed = {str(item).strip() for item in allowed_entity_types if str(item).strip()}
    allowed.add("其他")

    entities: list[dict[str, Any]] = []
    by_key: dict[tuple[str, str], dict[str, Any]] = {}
    raw_entities = payload.get("entities")
    if isinstance(raw_entities, list):
        for raw in raw_entities:
            if not isinstance(raw, dict):
                continue
            name = str(raw.get("name") or "").strip()
            if not name:
                continue
            normalized = normalize_entity_name(name)
            if not normalized:
                continue
            entity_type = str(raw.get("type") or "其他").strip() or "其他"
            if entity_type not in allowed:
                entity_type = "其他"
            key = (normalized, entity_type)
            description = str(raw.get("description") or "").strip() or None
            attributes = _coerce_attributes(raw.get("attributes"))
            existing = by_key.get(key)
            if existing:
                # Same entity mentioned twice in one chunk: keep the richer
                # description and merge attributes.
                if description and (not existing["description"] or len(description) > len(existing["description"])):
                    existing["description"] = description
                if attributes:
                    merged = dict(existing.get("attributes") or {})
                    merged.update(attributes)
                    existing["attributes"] = merged
                continue
            item = {
                "name": name[:255],
                "normalized_name": normalized,
                "entity_type": entity_type[:64],
                "description": description,
                "attributes": attributes,
            }
            by_key[key] = item
            entities.append(item)
            if len(entities) >= MAX_ENTITIES_PER_CHUNK:
                break

    relations: list[dict[str, Any]] = []
    seen_relations: set[tuple[str, str, str]] = set()
    raw_relations = payload.get("relations")
    if isinstance(raw_relations, list):
        for raw in raw_relations:
            if not isinstance(raw, dict):
                continue
            source = normalize_entity_name(str(raw.get("source") or ""))
            target = normalize_entity_name(str(raw.get("target") or ""))
            rel_type = str(raw.get("type") or "").strip()
            if not source or not target or not rel_type:
                continue
            # Endpoint must exist in this chunk's accepted entities.
            source_item = next((one for one in entities if one["normalized_name"] == source), None)
            target_item = next((one for one in entities if one["normalized_name"] == target), None)
            if not source_item or not target_item:
                continue
            key = (source, target, rel_type[:64])
            if key in seen_relations:
                continue
            seen_relations.add(key)
            try:
                weight = float(raw.get("weight", 0.7))
            except (TypeError, ValueError):
                weight = 0.7
            weight = max(0.0, min(1.0, weight))
            relations.append(
                {
                    "source_normalized": source,
                    "target_normalized": target,
                    "source_name": source_item["name"],
                    "target_name": target_item["name"],
                    "rel_type": rel_type[:64],
                    "description": str(raw.get("description") or "").strip() or None,
                    "weight": weight,
                    "attributes": _coerce_attributes(raw.get("attributes")),
                }
            )
            if len(relations) >= MAX_RELATIONS_PER_CHUNK:
                break

    return entities, relations


def extract_chunk_graph(
    *,
    knowledge_id: int,
    file_name: str,
    chunk_text: str,
    entity_types: list[str],
    model_id: int,
    invoke_user_id: int,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], int, int, int]:
    """Extract entities/relations from one chunk.

    Returns ``(entities, relations, token_in, token_out, failed_chunks)``.
    ``failed_chunks`` is 1 when all retries failed (the caller keeps going with
    the remaining chunks).
    """
    if not chunk_text or not chunk_text.strip():
        return [], [], 0, 0, 0

    prompt = get_prompt_manager_sync().render_prompt(
        PROMPT_NAMESPACE,
        PROMPT_NAME,
        entity_types="、".join(entity_types),
        file_name=file_name or "",
        chunk_text=chunk_text,
    )
    system_text = prompt.prompt.system
    user_text = prompt.prompt.user

    llm = LLMService.get_bisheng_llm_sync(
        model_id=model_id,
        app_id=ApplicationTypeEnum.KNOWLEDGE_GRAPH.value,
        app_name=ApplicationTypeEnum.KNOWLEDGE_GRAPH.value,
        app_type=ApplicationTypeEnum.KNOWLEDGE_GRAPH,
        user_id=invoke_user_id,
    )

    messages = [SystemMessage(content=system_text), HumanMessage(content=user_text)]
    total_in = 0
    total_out = 0
    last_error: Exception | None = None

    for attempt in range(MAX_RETRIES + 1):
        try:
            response = llm.invoke(messages)
            output_text = _content_to_text(getattr(response, "content", ""))
            token_in, token_out = _usage_from_response(response, system_text + user_text, output_text)
            total_in += token_in
            total_out += token_out
            payload = _json_loads(output_text)
            if not isinstance(payload, dict):
                raise ValueError("model output is not a JSON object")
            entities, relations = validate_extraction_payload(payload, entity_types)
            return entities, relations, total_in, total_out, 0
        except Exception as exc:
            last_error = exc
            logger.warning(
                "knowledge graph chunk extraction failed knowledge_id={} attempt={} error={}",
                knowledge_id,
                attempt + 1,
                exc,
            )
            if attempt >= MAX_RETRIES:
                break
            # Give the model a chance to correct malformed JSON.
            messages = [
                SystemMessage(content=system_text),
                HumanMessage(content=user_text),
                HumanMessage(
                    content=(
                        "上一次输出无法解析为合法 JSON。错误信息："
                        f"{exc}。请只输出符合约定结构的 JSON，不要输出代码块或解释。"
                    )
                ),
            ]

    logger.error(
        "knowledge graph chunk extraction exhausted retries knowledge_id={} error={}",
        knowledge_id,
        last_error,
    )
    return [], [], total_in, total_out, 1
