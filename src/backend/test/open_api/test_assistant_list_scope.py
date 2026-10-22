"""F076 increment: /assistant/list must prefilter, not post-filter (spec AC-45).

The DAO treats an empty ``assistant_ids`` list as "no filter", so the empty
allowlist case must short-circuit in the service *before* the DAO is called.
These tests capture the ids handed to the DAO instead of building assistant
rows, keeping the semantic (prefilter vs post-filter) the only assertion.
"""

from types import SimpleNamespace

import pytest

from bisheng.api.services import assistant as assistant_module
from bisheng.api.services.assistant import AssistantService


class _User:
    user_id = 1

    def is_admin(self) -> bool:
        return False


@pytest.fixture()
def captured(monkeypatch):
    calls = {"dao": [], "tag": []}

    def _dao(name, page, limit, assistant_ids=None, status=None):
        calls["dao"].append(list(assistant_ids or []))
        return [], 0

    def _tags(resource_type, ids):
        calls["tag"].append(list(ids or []))
        return {}

    def _filter(user, object_type, ids, permission_id):
        return list(ids)

    monkeypatch.setattr(
        assistant_module.AssistantDao, "get_all_assistants", classmethod(lambda cls, *a, **k: _dao(*a, **k))
    )
    monkeypatch.setattr(
        assistant_module.TagDao, "get_tags_by_resource", classmethod(lambda cls, *a, **k: _tags(*a, **k))
    )
    monkeypatch.setattr(
        assistant_module.ApplicationPermissionService,
        "filter_object_ids_by_permission_sync",
        staticmethod(_filter),
    )
    return calls


def test_empty_allowlist_short_circuits_without_touching_the_dao(captured):
    data, total = AssistantService.get_assistant(_User(), allowed_ids=set())

    assert (data, total) == ([], 0)
    assert captured["dao"] == [], "empty allowlist must not reach the DAO ([] means no filter)"


def test_allowlist_is_folded_into_the_dao_prefilter(captured):
    AssistantService.get_assistant(_User(), allowed_ids={"a1", "a2"})

    # tag_id absent -> the allowlist is the prefilter itself.
    assert captured["dao"] == [["a1", "a2"]] or sorted(captured["dao"][0]) == ["a1", "a2"]


def test_tag_filter_and_allowlist_intersect(captured, monkeypatch):
    monkeypatch.setattr(
        assistant_module.TagDao,
        "get_resources_by_tags",
        classmethod(
            lambda cls, tag_ids, resource_type: [SimpleNamespace(resource_id="a1"), SimpleNamespace(resource_id="a2")]
        ),
    )

    AssistantService.get_assistant(_User(), tag_id=5, allowed_ids={"a2"})

    assert captured["dao"] == [["a2"]]


def test_tag_and_allowlist_disjoint_returns_empty(captured, monkeypatch):
    monkeypatch.setattr(
        assistant_module.TagDao,
        "get_resources_by_tags",
        classmethod(lambda cls, tag_ids, resource_type: [SimpleNamespace(resource_id="a1")]),
    )

    data, total = AssistantService.get_assistant(_User(), tag_id=5, allowed_ids={"a2"})

    assert (data, total) == ([], 0)
    assert captured["dao"] == []
