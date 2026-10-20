"""F078 T-06: audit whitelist ↔ frontend mirror ↔ three-language lockstep."""

import json
from pathlib import Path

from bisheng.database.models.audit_log import (
    _UI_VISIBLE_V2_ACTIONS,
    _V2_NAMESPACE_TO_ACTION_PREFIX,
)
from bisheng.managed_agent.domain.constants import (
    UI_VISIBLE_ACTIONS,
    ManagedAgentAuditAction,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
LOG_TS = REPO_ROOT / "frontend" / "platform" / "src" / "controllers" / "API" / "log.ts"


def test_management_actions_are_ui_visible():
    missing = [a for a in UI_VISIBLE_ACTIONS if a not in _UI_VISIBLE_V2_ACTIONS]
    assert not missing, f"audit rows would be written but unreachable: {missing}"


def test_invoke_action_is_not_ui_visible():
    assert ManagedAgentAuditAction.INVOKE.value not in _UI_VISIBLE_V2_ACTIONS
    assert ManagedAgentAuditAction.INVOKE.value not in UI_VISIBLE_ACTIONS


def test_managed_agent_namespace_is_mapped():
    assert _V2_NAMESPACE_TO_ACTION_PREFIX.get("managed_agent") == "managed_agent."


def test_all_actions_share_the_namespace_prefix():
    for action in (*UI_VISIBLE_ACTIONS, ManagedAgentAuditAction.INVOKE.value):
        assert action.startswith("managed_agent."), action


def test_frontend_mirror_lists_every_action():
    if not LOG_TS.exists():  # pragma: no cover - frontend not checked out
        return
    content = LOG_TS.read_text(encoding="utf-8")
    missing = [a for a in UI_VISIBLE_ACTIONS if f"value: '{a}'" not in content]
    assert not missing, (
        f"frontend log.ts is missing these actions, so the audit page filter will not offer them: {missing}"
    )
    assert "{ name: 'log.systemIdEnum.managedAgent', value: 'managed_agent' }" in content
    assert "case 'managed_agent': return actions.filter(a => a.value.startsWith('managed_agent.'))" in content
    assert "value: 'managed_agent.invoke'" not in content


def test_frontend_i18n_keys_exist_in_all_three_languages():
    required = ["managedAgent"]
    required += [
        "managedAgent" + "".join(part.capitalize() for chunk in a.split(".")[1:] for part in chunk.split("_"))
        for a in UI_VISIBLE_ACTIONS
    ]
    for lang in ("zh-Hans", "en-US", "ja"):
        path = REPO_ROOT / "frontend" / "platform" / "public" / "locales" / lang / "bs.json"
        if not path.exists():  # pragma: no cover - frontend not checked out
            return
        data = json.loads(path.read_text(encoding="utf-8"))
        for key in required:
            group = "systemIdEnum" if key == "managedAgent" else "eventTypeEnum"
            assert key in data["log"][group], f"{lang} bs.json missing log.{group}.{key}"
