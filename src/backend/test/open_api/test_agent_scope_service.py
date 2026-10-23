"""`list_assignable_agents` 的 invocable 口径(2026-10-22)。

assistant 与 workflow 一样,只有 `use_app`(editor 档起)才算可调用;
`view_app` 仅够详情/列表读取。
"""

from types import SimpleNamespace

from bisheng.open_api.domain.services import agent_scope_service as svc


async def _run(monkeypatch, *, use_app: bool):
    from bisheng.permission.domain.services.application_permission_service import ApplicationPermissionService

    async def fake_list_grants(owner_user_id):
        return [
            {"resource_type": "assistant", "resource_id": "a1", "level": "viewer"},
            {"resource_type": "workflow", "resource_id": "w1", "level": "viewer"},
        ]

    async def fake_names(rows):
        return {}

    async def fake_has_any(user, object_type, object_id, permission_ids):
        calls.append((object_type, tuple(permission_ids)))
        return use_app and "use_app" in permission_ids

    calls = []
    monkeypatch.setattr(svc, "list_grants", fake_list_grants)
    monkeypatch.setattr(svc, "_load_names", fake_names)
    monkeypatch.setattr(ApplicationPermissionService, "has_any_permission_async", fake_has_any)

    rows = await svc.list_assignable_agents(SimpleNamespace(user_id=3))
    return {row["type"]: row for row in rows}, calls


async def test_assistant_invocable_requires_use_app(monkeypatch):
    rows, calls = await _run(monkeypatch, use_app=False)

    assert rows["assistant"]["invocable"] is False
    assert rows["workflow"]["invocable"] is False
    assert ("assistant", ("use_app",)) in calls
    # viewer-only 不再被写成可调用。
    assert all("view_app" not in permission_ids for _, permission_ids in calls)


async def test_assistant_invocable_true_for_editor(monkeypatch):
    rows, calls = await _run(monkeypatch, use_app=True)

    assert rows["assistant"]["invocable"] is True
    assert rows["workflow"]["invocable"] is True
    assert ("assistant", ("use_app",)) in calls
