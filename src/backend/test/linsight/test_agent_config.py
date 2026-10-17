"""Configuration rejection tests, independent of model and storage services."""

import pytest
from pydantic import ValidationError

from bisheng.linsight.domain.schemas.agent_config import AgentConfig, SubagentConfig


def member(**changes):
    return {"id": "researcher", "name": "Researcher", "description": "Find evidence", **changes}


@pytest.mark.parametrize(
    "changes",
    [
        {"id": "../escape"},
        {"name": "  "},
        {"description": ""},
        {"tool_refs": [True]},
        {"tool_refs": [-1]},
        {"tool_refs": [1, 1]},
        {"tool_refs": [1], "connector_refs": [1]},
        {"skill_names": ["../secret"]},
        {"model_id": "https://untrusted"},
        {"credentials": "forbidden"},
    ],
)
def test_invalid_member_rejected(changes):
    with pytest.raises(ValidationError):
        SubagentConfig.model_validate(member(**changes))


def test_duplicate_and_excess_members_rejected():
    with pytest.raises(ValidationError):
        AgentConfig(subagents=[member(), member()])
    with pytest.raises(ValidationError):
        AgentConfig(subagents=[member(id=f"member-{i}") for i in range(6)])


def test_optional_capabilities_do_not_inherit():
    config = AgentConfig(subagents=[member()])
    selected = config.subagents[0]
    assert selected.model_id is None
    assert selected.skill_names == selected.tool_refs == selected.connector_refs == []
    assert AgentConfig().subagents == []
