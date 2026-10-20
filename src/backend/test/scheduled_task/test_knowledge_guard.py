"""F077 T-13/T-14：创建期绑定校验与知识可见性校验。

- `collect_workflow_refs` 是纯函数（解析 `flow.data` 的节点参数形状），单测直接喂结构；
- `assert_app_knowledge_readable` / `validate_binding` 的协作者全部 mock——它们只做
  编排与判定，真正的权限判定在 `PermissionService`（由 T-09/既有测试覆盖）。
"""

import pytest

from bisheng.common.errcode.scheduled_task import ScheduledTaskKnowledgeUnauthorizedError
from bisheng.database.models.flow import FlowType
from bisheng.scheduled_task.domain.services import knowledge_guard, trigger_guard
from bisheng.scheduled_task.domain.services.knowledge_guard import (
    KnowledgeRef,
    collect_workflow_refs,
)
from bisheng.scheduled_task.domain.services.trigger_guard import IdentityState, validate_binding

TENANT_ID = 7


# ── 纯函数：flow.data 解析 ───────────────────────────────────────────


def _flow_data(*nodes):
    return {"nodes": list(nodes), "edges": []}


def _node(params):
    return {"id": "n1", "type": "rag", "data": {"group_params": [{"params": params}]}}


def test_collect_refs_reads_new_and_legacy_switch_shapes():
    data = _flow_data(
        _node(
            [
                {"key": "knowledge", "value": {"type": "knowledge", "value": [{"key": 11}, {"key": "12"}]}},
                {"key": "advanced_retrieval_switch", "value": {"user_auth": True}},
            ]
        ),
        _node(
            [
                {"key": "knowledge", "value": {"type": "space", "value": [{"key": 21}]}},
                {"key": "user_auth", "value": True},  # 旧形态：顶层 user_auth
            ]
        ),
        _node([{"key": "prompt", "value": "hi"}]),  # 无知识参数
    )
    refs = collect_workflow_refs(data)
    assert refs == [
        KnowledgeRef(kind="knowledge", id=11, user_auth=True),
        KnowledgeRef(kind="knowledge", id=12, user_auth=True),
        KnowledgeRef(kind="space", id=21, user_auth=True),
    ]


def test_collect_refs_defaults_to_user_auth_off_and_skips_junk():
    data = _flow_data(
        _node([{"key": "knowledge", "value": {"type": "knowledge", "value": [{"key": "not-an-id"}, {"key": 31}]}}])
    )
    refs = collect_workflow_refs(data)
    assert refs == [KnowledgeRef(kind="knowledge", id=31, user_auth=False)]


def test_collect_refs_empty_or_malformed():
    assert collect_workflow_refs({}) == []
    assert collect_workflow_refs({"nodes": [{"data": None}]}) == []


# ── 编排：开关为开的引用必须可读（11913） ────────────────────────────


@pytest.fixture()
def wire(monkeypatch):
    state = {
        "refs": [],
        "readable_kb": set(),
        "readable_space": set(),
        "judge_called": False,
    }

    async def _collect(app_id, app_type):
        return state["refs"]

    async def _get_user(user_id):
        return type("U", (), {"user_name": "svc-user"})()

    def _judge(*, user_name, knowledge_ids):
        state["judge_called"] = True
        return [type("K", (), {"id": i})() for i in knowledge_ids if i in state["readable_kb"]]

    async def _can_read(user_id, space_id):
        return space_id in state["readable_space"]

    monkeypatch.setattr(knowledge_guard, "collect_app_refs", _collect)
    monkeypatch.setattr(knowledge_guard.UserDao, "aget_user", _get_user)
    monkeypatch.setattr(knowledge_guard.KnowledgeDao, "judge_knowledge_permission", _judge)
    monkeypatch.setattr(knowledge_guard.KnowledgeSpaceService, "_user_can_read_space", _can_read)
    return state


async def test_switch_off_is_not_checked(wire):
    wire["refs"] = [KnowledgeRef(kind="knowledge", id=11, user_auth=False)]
    await knowledge_guard.assert_app_knowledge_readable(
        app_id="1001", app_type=FlowType.WORKFLOW.value, owner_user_id=501, tenant_id=TENANT_ID
    )
    assert wire["judge_called"] is False, "OFF 档不查（配置者可见范围是 F041 的有意共享）"


async def test_unreadable_knowledge_raises_11913_with_ids(wire):
    wire["refs"] = [
        KnowledgeRef(kind="knowledge", id=11, user_auth=True),
        KnowledgeRef(kind="knowledge", id=12, user_auth=True),
        KnowledgeRef(kind="space", id=21, user_auth=True),
    ]
    wire["readable_kb"] = {11}
    wire["readable_space"] = set()

    with pytest.raises(ScheduledTaskKnowledgeUnauthorizedError) as excinfo:
        await knowledge_guard.assert_app_knowledge_readable(
            app_id="1001", app_type=FlowType.WORKFLOW.value, owner_user_id=501, tenant_id=TENANT_ID
        )
    assert excinfo.value.Code == 11913
    assert excinfo.value.kwargs["knowledge_ids"] == [12, 21]


async def test_all_readable_passes(wire):
    wire["refs"] = [
        KnowledgeRef(kind="knowledge", id=11, user_auth=True),
        KnowledgeRef(kind="space", id=21, user_auth=True),
    ]
    wire["readable_kb"] = {11}
    wire["readable_space"] = {21}
    await knowledge_guard.assert_app_knowledge_readable(
        app_id="1001", app_type=FlowType.WORKFLOW.value, owner_user_id=501, tenant_id=TENANT_ID
    )


# ── 创建期绑定校验（AC-20） ─────────────────────────────────────────


class _Account:
    def __init__(self, *, owner_user_id=501, enabled=True):
        self.resource_owner_user_id = owner_user_id
        self.is_enabled = enabled


@pytest.fixture()
def binding_wire(monkeypatch):
    state = {"app_exists": True, "account": _Account(), "payload": object(), "privileged": False, "use_app": True}

    async def _exists(app_id, app_type):
        return state["app_exists"]

    async def _scoped(account_id, *, tenant_id):
        return state["account"]

    async def _load(*, owner_user_id, tenant_id):
        return state["payload"]

    async def _priv(payload, tenant_id):
        return state["privileged"]

    async def _has_use_app(login_user, object_type, object_id, permission_ids):
        return state["use_app"]

    monkeypatch.setattr(trigger_guard, "_app_exists", _exists)
    monkeypatch.setattr(trigger_guard.ServiceAccountDao, "aget_scoped", _scoped)
    monkeypatch.setattr(trigger_guard, "load_owner_payload", _load)
    monkeypatch.setattr(trigger_guard, "owner_is_privileged", _priv)
    monkeypatch.setattr(trigger_guard.ApplicationPermissionService, "has_any_permission_async", _has_use_app)
    return state


async def test_binding_happy_path_locks_owner(binding_wire):
    app_ok, binding = await validate_binding(
        app_id="1001", app_type=FlowType.WORKFLOW.value, service_account_id=5, tenant_id=TENANT_ID
    )
    assert app_ok is True
    assert binding.ok is True
    assert binding.owner_user_id == 501, "创建通过后把该账号的 owner 锁进任务行（INV-25）"


async def test_binding_app_missing(binding_wire):
    binding_wire["app_exists"] = False
    app_ok, _ = await validate_binding(
        app_id="1001", app_type=FlowType.WORKFLOW.value, service_account_id=5, tenant_id=TENANT_ID
    )
    assert app_ok is False


@pytest.mark.parametrize(
    ("mutate", "expected"),
    [
        (lambda s: s.update(account=_Account(enabled=False)), IdentityState.ACCOUNT_UNAVAILABLE),
        (lambda s: s.update(payload=None), IdentityState.OWNER_UNAVAILABLE),
        (lambda s: s.update(privileged=True), IdentityState.OWNER_PRIVILEGED),
        (lambda s: s.update(use_app=False), IdentityState.NO_USE_APP),
    ],
)
async def test_binding_identity_rejections(binding_wire, mutate, expected):
    mutate(binding_wire)
    app_ok, binding = await validate_binding(
        app_id="1001", app_type=FlowType.WORKFLOW.value, service_account_id=5, tenant_id=TENANT_ID
    )
    assert app_ok is True, "应用没问题，身份有问题 —— 两类错误要能区分（AC-20）"
    assert binding.identity_state is expected
    assert binding.owner_user_id is None
