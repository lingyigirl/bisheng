"""Skill model migration preserves live permissions and rejects stale grants."""

import copy
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from bisheng.core.openfga.authorization_model import get_authorization_model
from scripts.migrate_fga_skill_permissions import add_skill_type, canonical, owner_matches, run


def test_additive_model_preserves_live_types_and_conditions():
    shipped = get_authorization_model()
    live = copy.deepcopy(shipped)
    live["id"] = "old"
    live["type_definitions"] = [t for t in live["type_definitions"] if t["type"] != "linsight_skill"]
    live["type_definitions"].append({"type": "custom_company_resource"})
    live["conditions"] = {"custom_condition": {"name": "custom_condition", "expression": "true"}}
    before = copy.deepcopy(live)
    result = add_skill_type(live, shipped)
    assert live == before
    assert "id" not in result
    assert result["type_definitions"][:-1] == before["type_definitions"]
    assert result["conditions"] == live["conditions"]
    assert result["type_definitions"][-1]["type"] == "linsight_skill"
    assert add_skill_type(result, shipped) is None


def test_existing_conflicting_skill_model_is_refused():
    live = get_authorization_model()
    next(t for t in live["type_definitions"] if t["type"] == "linsight_skill")["relations"]["owner"] = {
        "computedUserset": {"relation": "viewer"}
    }
    with pytest.raises(ValueError, match="differs"):
        add_skill_type(live, get_authorization_model())


def test_optional_protobuf_defaults_do_not_hide_userset_changes():
    assert canonical({"this": {}, "condition": "", "relation": ""}) == {"this": {}}
    assert canonical({"this": {}}) != canonical({})
    assert canonical({"computedUserset": {"object": "", "relation": "owner"}}) == {
        "computedUserset": {"relation": "owner"}
    }


@pytest.mark.parametrize(
    "change",
    [
        {"tenant_id": 2},
        {"action": "delete"},
        {"relation": "viewer"},
        {"object": "linsight_skill:6"},
        {"fga_user": "user:99"},
        {"status": "succeeded"},
    ],
)
def test_owner_repair_rejects_other_tenants_changed_owners_and_unrelated_operations(change):
    item = SimpleNamespace(
        tenant_id=1, action="write", relation="owner", object="linsight_skill:5", fga_user="user:7", status="dead"
    )
    skill = SimpleNamespace(id=5, tenant_id=1, created_by=7)
    assert owner_matches(item, skill, 1)
    assert not owner_matches(item, None, 1)
    item.__dict__.update(change)
    assert not owner_matches(item, skill, 1)
    assert not owner_matches(item, SimpleNamespace(id=5, tenant_id=2, created_by=7), 1)


async def test_model_publication_guard_and_read_only_preview(monkeypatch):
    from bisheng.core.openfga import manager

    live = get_authorization_model()
    live["type_definitions"] = [t for t in live["type_definitions"] if t["type"] != "linsight_skill"]
    client = SimpleNamespace(
        model_id="old",
        store_id="store",
        read_authorization_model=AsyncMock(return_value=live),
        write_authorization_model=AsyncMock(),
    )
    monkeypatch.setattr(manager, "aget_fga_client", AsyncMock(return_value=client))
    args = SimpleNamespace(expected_model_id=None, repair_owners=False, apply=False)
    report = {}
    await run(args, report)
    assert report["status"] == "would_add_skill_type"
    client.write_authorization_model.assert_not_awaited()
    args.apply = True
    with pytest.raises(ValueError, match="requires"):
        await run(args, {})
    args.expected_model_id = "unexpected"
    with pytest.raises(ValueError, match="differs"):
        await run(args, {})
    client.write_authorization_model.assert_not_awaited()


async def test_model_apply_readback_and_rerun_are_idempotent(monkeypatch):
    from bisheng.core.openfga import manager

    live = get_authorization_model()
    live["type_definitions"] = [t for t in live["type_definitions"] if t["type"] != "linsight_skill"]
    expected = add_skill_type(live, get_authorization_model())
    client = SimpleNamespace(
        model_id="old",
        store_id="store",
        read_authorization_model=AsyncMock(side_effect=[live, {**expected, "id": "new"}]),
        write_authorization_model=AsyncMock(return_value="new"),
    )
    monkeypatch.setattr(manager, "aget_fga_client", AsyncMock(return_value=client))
    args = SimpleNamespace(expected_model_id="old", repair_owners=False, apply=True)
    report = {}
    await run(args, report)
    assert report["new_model_id"] == "new"
    client.write_authorization_model.assert_awaited_once_with(expected)
    client.read_authorization_model = AsyncMock(return_value=expected)
    client.write_authorization_model.reset_mock()
    await run(args, {})
    client.write_authorization_model.assert_not_awaited()
