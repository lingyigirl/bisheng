"""F078 T-10: Agent Card parsing and scheduled preset validation."""

import pytest

from bisheng.common.errcode.managed_agent import ManagedAgentConfigInvalidError
from bisheng.managed_agent.domain.services.agent_card import (
    AgentCardFileInput,
    parse_agent_card,
    validate_preset_inputs,
)

CARD = {
    "version": "v1",
    "capabilities": ["chat"],
    "inputs": [
        {"key": "query", "label": "问题", "type": "string", "required": True},
        {"key": "top_k", "label": "条数", "type": "integer", "default": 3},
        {"key": "debug", "label": "调试", "type": "boolean", "default": False},
    ],
}


def test_parse_card_and_defaults():
    card = parse_agent_card(CARD)
    assert card.version == "v1"
    assert card.input_map()["query"].required is True
    assert card.input_map()["top_k"].default == 3


def test_none_card_is_empty_card():
    card = parse_agent_card(None)
    assert card.inputs == []


@pytest.mark.parametrize(
    "raw",
    [
        {"inputs": [{"key": "x", "type": "weird"}]},
        {"inputs": [{"key": "x", "type": "string"}, {"key": "x", "type": "string"}]},
        "not-a-dict",
    ],
)
def test_invalid_card_rejected(raw):
    with pytest.raises(ManagedAgentConfigInvalidError):
        parse_agent_card(raw)


def test_preset_validation_coerces_types_and_fills_defaults():
    card = parse_agent_card(CARD)
    presets = validate_preset_inputs(
        card,
        [{"key": "query", "value": "hello"}, {"key": "top_k", "value": "5"}, {"key": "debug", "value": "true"}],
    )
    values = {item.key: item.value for item in presets}
    assert values == {"query": "hello", "top_k": "5", "debug": "True"}


def test_unknown_preset_key_rejected():
    card = parse_agent_card(CARD)
    with pytest.raises(ManagedAgentConfigInvalidError):
        validate_preset_inputs(card, [{"key": "nope", "value": "x"}])


def test_missing_required_preset_rejected():
    card = parse_agent_card(CARD)
    with pytest.raises(ManagedAgentConfigInvalidError):
        validate_preset_inputs(card, [{"key": "top_k", "value": "5"}])


def test_type_mismatch_rejected():
    card = parse_agent_card(CARD)
    with pytest.raises(ManagedAgentConfigInvalidError):
        validate_preset_inputs(card, [{"key": "query", "value": "x"}, {"key": "top_k", "value": "abc"}])


def test_file_input_is_rejected_for_scheduling():
    card = parse_agent_card({"inputs": [{"key": "file", "type": "string", "file": True, "required": True}]})
    with pytest.raises(AgentCardFileInput):
        validate_preset_inputs(card, [{"key": "file", "value": "x"}])
