"""Unit tests for the saved-collaborator library (F087, 2026-10-16).

Locks the trust model that makes a personal member library safe to add on top
of the task-time collaborator validation:

- SAVE runs the SAME gate a task submission would (AgentConfigService.resolve)
  and only then upserts;
- LIST reports availability issues with BATCHED queries (one tools query, one
  skills query for the whole library — not per member);
- every DAO call is scoped to the requesting user.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from bisheng.common.errcode.linsight import LinsightAgentConfigurationError
from bisheng.linsight.domain.models.linsight_agent_member import LinsightAgentMember
from bisheng.linsight.domain.schemas.member_schema import MemberSaveRequest
from bisheng.linsight.domain.services import member_service as member_service_module
from bisheng.linsight.domain.services.member_service import MemberService

USER = SimpleNamespace(user_id=7, tenant_id=1)


class _DaoStub:
    def __init__(self, rows=None, by_id=None, by_name=None):
        self.rows = rows or []
        self.by_id = by_id or {}
        self.by_name = by_name or {}
        self.upserted = []
        self.updated = []
        self.deleted = []
        self.defaults = []

    async def list_by_user(self, user_id):
        self.last_list_user = user_id
        return self.rows

    async def get_by_id(self, member_id, user_id):
        return self.by_id.get(member_id)

    async def get_by_name(self, user_id, name):
        return self.by_name.get(name)

    async def upsert(self, member):
        member.id = 11
        self.upserted.append(member)
        return member

    async def update(self, member):
        self.updated.append(member)
        return member

    async def delete(self, member_id, user_id):
        self.deleted.append((member_id, user_id))
        return True

    async def set_default(self, member_id, user_id, value):
        self.defaults.append((member_id, user_id, value))
        return True


class _RepoStub:
    def __init__(self, tools=None, skills=None):
        self._tools = tools or []
        self._skills = skills or []
        self.tool_calls = []
        self.skill_calls = 0

    async def tools(self, ids):
        self.tool_calls.append(list(ids))
        return [t for t in self._tools if t.id in ids]

    async def enabled_skills(self):
        self.skill_calls += 1
        return self._skills


def _row(**overrides) -> LinsightAgentMember:
    base = {
        "id": 1,
        "tenant_id": 1,
        "user_id": USER.user_id,
        "name": "财务分析师",
        "description": "看报表",
        "system_prompt": "",
        "model_id": None,
        "skill_names": [],
        "tool_refs": [],
        "connector_refs": [],
        "is_default": False,
    }
    base.update(overrides)
    return LinsightAgentMember(**base)


async def test_save_validates_with_task_gate_then_upserts(monkeypatch):
    captured = {}

    async def _resolve(self, config, user, tenant_id, main_model, **kwargs):
        captured["config"] = config
        captured["user"] = user
        captured["tenant_id"] = tenant_id

    monkeypatch.setattr(member_service_module.AgentConfigService, "resolve", _resolve)
    dao = _DaoStub()
    service = MemberService(repository=_RepoStub(), dao=dao)

    result = await service.save_member(
        USER,
        MemberSaveRequest(name="财务分析师", description="看报表", tool_refs=[45], skill_names=["excel"]),
    )

    member = captured["config"].subagents[0]
    assert member.name == "财务分析师"
    assert member.tool_refs == [45]
    assert member.skill_names == ["excel"]
    assert captured["tenant_id"] == 1

    assert len(dao.upserted) == 1
    row = dao.upserted[0]
    assert row.user_id == USER.user_id
    assert row.tenant_id == 1
    assert result["id"] == 11
    assert result["issues"] == {}
    assert result["is_default"] is False


async def test_save_with_id_updates_the_owned_row_in_place(monkeypatch):
    async def _resolve(self, config, user, tenant_id, main_model, **kwargs):
        return None

    monkeypatch.setattr(member_service_module.AgentConfigService, "resolve", _resolve)
    target = _row(id=3, name="旧名字")
    dao = _DaoStub(by_id={3: target})
    service = MemberService(repository=_RepoStub(), dao=dao)

    result = await service.save_member(
        USER, MemberSaveRequest(id=3, name="新名字", description="改过了")
    )

    assert result["name"] == "新名字"
    assert dao.updated == [target] and dao.upserted == []
    assert target.description == "改过了"


async def test_save_with_id_rejects_rename_collision(monkeypatch):
    async def _resolve(self, config, user, tenant_id, main_model, **kwargs):
        return None

    monkeypatch.setattr(member_service_module.AgentConfigService, "resolve", _resolve)
    target = _row(id=3, name="旧名字")
    other = _row(id=4, name="占用的名字")
    dao = _DaoStub(by_id={3: target}, by_name={"占用的名字": other})
    service = MemberService(repository=_RepoStub(), dao=dao)

    with pytest.raises(LinsightAgentConfigurationError):
        await service.save_member(USER, MemberSaveRequest(id=3, name="占用的名字", description="x"))
    assert dao.updated == [] and dao.upserted == []


async def test_save_with_unknown_id_returns_none(monkeypatch):
    async def _resolve(self, config, user, tenant_id, main_model, **kwargs):
        return None

    monkeypatch.setattr(member_service_module.AgentConfigService, "resolve", _resolve)
    dao = _DaoStub()
    service = MemberService(repository=_RepoStub(), dao=dao)

    assert await service.save_member(USER, MemberSaveRequest(id=999, name="x", description="y")) is None
    assert dao.updated == [] and dao.upserted == []


async def test_set_default_scopes_to_the_requesting_user():
    dao = _DaoStub()
    service = MemberService(repository=_RepoStub(), dao=dao)
    assert await service.set_default(USER, 8, True) is True
    assert dao.defaults == [(8, USER.user_id, True)]


async def test_save_propagates_validation_rejection(monkeypatch):
    async def _resolve(self, config, user, tenant_id, main_model, **kwargs):
        raise LinsightAgentConfigurationError(msg="Selected tool is unavailable")

    monkeypatch.setattr(member_service_module.AgentConfigService, "resolve", _resolve)
    dao = _DaoStub()
    service = MemberService(repository=_RepoStub(), dao=dao)

    with pytest.raises(LinsightAgentConfigurationError):
        await service.save_member(USER, MemberSaveRequest(name="x", description="y", tool_refs=[999]))
    assert dao.upserted == [], "a rejected template must never be stored"


async def test_list_reports_issues_with_batched_queries(monkeypatch):
    rows = [
        _row(id=1, name="A", tool_refs=[45, 999], skill_names=["excel", "ghost"], model_id="3"),
        _row(id=2, name="B", connector_refs=[77], model_id="4"),
    ]
    tools = [SimpleNamespace(id=45, is_delete=0), SimpleNamespace(id=77, is_delete=1)]
    repo = _RepoStub(tools=tools, skills=[SimpleNamespace(name="excel")])

    async def _model_for_call(model_id):
        return SimpleNamespace(online=model_id != 4, model_type="llm")

    monkeypatch.setattr(member_service_module.LLMService, "get_model_for_call", staticmethod(_model_for_call))

    service = MemberService(repository=repo, dao=_DaoStub(rows=rows))
    out = await service.list_members(USER)

    assert [m["name"] for m in out] == ["A", "B"]
    # A: deleted tool 999 + disabled skill + online model
    assert out[0]["issues"] == {"tools": [999], "skills": ["ghost"]}
    # B: connector 77 is deleted + its model is offline
    assert out[1]["issues"] == {"tools": [77], "model": True}
    # Batched: ONE tools query for the whole library, ONE skills query.
    assert repo.tool_calls == [[45, 77, 999]]
    assert repo.skill_calls == 1


async def test_delete_scopes_to_the_requesting_user():
    dao = _DaoStub()
    service = MemberService(repository=_RepoStub(), dao=dao)
    assert await service.delete_member(USER, 11) is True
    assert dao.deleted == [(11, USER.user_id)]


def test_member_routes_are_registered():
    """A new endpoint 404s until it is included in the router — guard it."""
    from bisheng.linsight.api.router import router

    paths = {route.path for route in router.routes}
    assert "/linsight/member" in paths
    assert "/linsight/member/{member_id}" in paths
