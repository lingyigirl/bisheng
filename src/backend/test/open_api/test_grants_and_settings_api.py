"""F076 T-22:租户设置 + 资源授权代管的契约测试。

- 设置:缺行=未启用;PUT 立即生效(清缓存);`effective = 部署级 ∧ 租户级` 回显;
- 代管:两道闸(白名单 11820 / 管理员须对资源有 manager 11808);授予时**主体恒为
  专用身份**(不暴露 subject 给前端);撤销不存在的授权 → 11809。
"""

import pathlib
from contextlib import asynccontextmanager

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import StaticPool

from bisheng.common.errcode.open_api import (
    OpenApiNotFoundError,
    OpenApiResourceGrantsUnavailableError,
    OpenApiResourceNotGrantedError,
    OpenApiResourceTypeUnsupportedError,
)
from bisheng.core.openfga.exceptions import FGAConnectionError
from bisheng.database.models.tenant import UserTenant
from bisheng.open_api.api.endpoints import grants as grants_api
from bisheng.open_api.api.endpoints import settings as settings_api
from bisheng.open_api.domain.models.api_credential import ApiCredential
from bisheng.open_api.domain.models.open_api_tenant_setting import OpenApiTenantSetting
from bisheng.open_api.domain.models.service_account import ServiceAccount
from bisheng.open_api.domain.services import identity_service
from bisheng.open_api.domain.services import service_account_service as svc
from bisheng.permission.domain.services.permission_service import PermissionService
from bisheng.user.domain.models.user import User

TABLES = [
    User.__table__,
    UserTenant.__table__,
    ServiceAccount.__table__,
    ApiCredential.__table__,
    OpenApiTenantSetting.__table__,
]
TENANT_ID = 71
OTHER_TENANT = 72
OWNER_ID = 401


class _Admin:
    def __init__(self, tenant_id: int = TENANT_ID, user_id: int = 7):
        self.tenant_id = tenant_id
        self.user_id = user_id


class _FakeRedis:
    def __init__(self) -> None:
        self.store: dict[str, object] = {}

    async def aset(self, key, value, expiration=3600):
        self.store[key] = value
        return True

    async def aget(self, key):
        return self.store.get(key)

    async def adelete(self, key):
        self.store.pop(key, None)
        return 1


@pytest.fixture()
async def g_engine():
    engine = create_async_engine(
        "sqlite+aiosqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(lambda sync_conn: ServiceAccount.metadata.create_all(sync_conn, tables=TABLES))
    yield engine
    await engine.dispose()


@pytest.fixture()
async def patched_db(g_engine, monkeypatch):
    @asynccontextmanager
    async def _factory():
        async with AsyncSession(bind=g_engine, expire_on_commit=False) as session:
            yield session

    monkeypatch.setattr("bisheng.open_api.domain.models.dao.get_async_db_session", _factory)
    monkeypatch.setattr("bisheng.open_api.domain.services.service_account_service.get_async_db_session", _factory)
    yield g_engine


@pytest.fixture()
def fake_redis(monkeypatch):
    fake = _FakeRedis()

    async def _get():
        return fake

    from bisheng.open_api.domain.services import credential_service, tenant_setting_service

    monkeypatch.setattr(credential_service, "_get_redis", _get)
    monkeypatch.setattr(tenant_setting_service, "_get_redis", _get)
    return fake


@pytest.fixture()
async def account(patched_db, monkeypatch):
    async def _init(user_id: int, user_name: str, tenant_id: int | None = None, **kwargs):
        class _P:
            is_global_super = False

            async def has_tenant_admin(self, tenant_id: int) -> bool:
                return False

        return _P()

    monkeypatch.setattr(identity_service.UserPayload, "init_login_user", _init)
    acct = await svc.create_service_account(tenant_id=TENANT_ID, name="对接系统")
    # 断言后续代管用的主体是专用身份
    assert acct.resource_owner_user_id == OWNER_ID or acct.resource_owner_user_id > 0
    return acct


def _stub_permission(
    monkeypatch,
    *,
    admin_can_manage: bool,
    grants_by_type: dict | None = None,
    levels: dict | None = None,
    direct: dict | None = None,
):
    calls = {"authorize": [], "check": [], "identity": []}
    grants_by_type = grants_by_type if grants_by_type is not None else {}
    levels = levels if levels is not None else {}
    #: (resource_type, resource_id) -> 该身份直接持有的档位元组关系集合。
    direct = direct if direct is not None else {}

    async def _check(user_id, relation, object_type, object_id, login_user=None, consistency=None):
        calls["check"].append((user_id, relation, object_type, object_id))
        return admin_can_manage

    async def _list_accessible_ids(user_id, relation, object_type, login_user=None):
        # 传进来的必须是专用身份自己:管理员会拿到 None(引擎短路),列表就恒空。
        calls["identity"].append(login_user)
        return grants_by_type.get(object_type, [])

    async def _level(user_id, object_type, object_id, login_user=None):
        return levels.get((object_type, str(object_id)))

    async def _direct(user_id, object_type, object_id):
        return set(direct.get((object_type, str(object_id)), set()))

    async def _authorize(object_type, object_id, grants=None, revokes=None, enforce_fga_success=False):
        calls["authorize"].append({"object": f"{object_type}:{object_id}", "grants": grants, "revokes": revokes})

    monkeypatch.setattr(PermissionService, "check", _check)
    monkeypatch.setattr(PermissionService, "list_accessible_ids", _list_accessible_ids)
    monkeypatch.setattr(PermissionService, "get_permission_level", _level)
    monkeypatch.setattr(PermissionService, "list_direct_relations", _direct)
    monkeypatch.setattr(PermissionService, "authorize", _authorize)
    return calls


# ---------------------------------------------------------------------------
# 设置端点
# ---------------------------------------------------------------------------


async def test_settings_missing_row_means_not_enforced(patched_db, fake_redis, monkeypatch):
    monkeypatch.setattr(
        settings_api.app_settings, "open_api", type(settings_api.app_settings.open_api)(enforce_keys=True)
    )
    response = await settings_api.get_settings(login_user=_Admin())
    assert response.data == {
        "tenant_id": TENANT_ID,
        "enforce_keys": False,
        "deployment_enforced": True,
        "effective": False,
        "update_time": None,
    }


async def test_settings_put_takes_effect_and_clears_cache(patched_db, fake_redis, monkeypatch):
    monkeypatch.setattr(
        settings_api.app_settings, "open_api", type(settings_api.app_settings.open_api)(enforce_keys=True)
    )

    updated = await settings_api.update_settings(settings_api.TenantSettingReq(enforce_keys=True), login_user=_Admin())
    assert updated.data["enforce_keys"] is True and updated.data["effective"] is True
    assert fake_redis.store == {}, "upstream 写后应清缓存(立即生效)"

    off = await settings_api.update_settings(settings_api.TenantSettingReq(enforce_keys=False), login_user=_Admin())
    assert off.data["effective"] is False


async def test_settings_effective_requires_deployment_switch(patched_db, fake_redis, monkeypatch):
    monkeypatch.setattr(
        settings_api.app_settings, "open_api", type(settings_api.app_settings.open_api)(enforce_keys=False)
    )
    updated = await settings_api.update_settings(settings_api.TenantSettingReq(enforce_keys=True), login_user=_Admin())
    assert updated.data["deployment_enforced"] is False and updated.data["effective"] is False


# ---------------------------------------------------------------------------
# 授权代管
# ---------------------------------------------------------------------------


async def test_list_grants_returns_rows(patched_db, fake_redis, account, monkeypatch):
    calls = _stub_permission(
        monkeypatch,
        admin_can_manage=True,
        grants_by_type={"assistant": [11], "knowledge_library": [22]},
        levels={("assistant", "11"): "viewer", ("knowledge_library", "22"): "manager"},
        direct={("assistant", "11"): {"viewer"}},
    )
    response = await grants_api.list_resource_grants(account.id, login_user=_Admin())
    assert response.data["total"] == 2
    assert {(r["resource_type"], r["resource_id"], r["level"]) for r in response.data["data"]} == {
        ("assistant", "11", "viewer"),
        ("knowledge_library", "22", "manager"),
    }
    # 来源:直接授予 vs 继承可见性(后者不归代管管理,不可撤销)。
    sources = {(r["resource_type"], r["source"]) for r in response.data["data"]}
    assert sources == {("assistant", "direct"), ("knowledge_library", "inherited")}
    # 回归钉子:查询必须用专用身份,绝不能把管理员传进引擎(会短路成空列表/最高档)。
    assert calls["identity"], "list_accessible_ids 未被调用"
    # 一律不带用户上下文(带管理员会短路成空列表/最高档,见 list_grants docstring)。
    assert set(calls["identity"]) == {None}  # 每个资源类型一次查询,均不带上下文


async def test_list_grants_normalises_engine_vocabulary(patched_db, fake_redis, account, monkeypatch):
    """引擎档位(can_read/can_edit/can_manage)对外一律归一成 viewer/editor/manager。"""
    _stub_permission(
        monkeypatch,
        admin_can_manage=True,
        grants_by_type={"assistant": [11, 12]},
        levels={("assistant", "11"): "can_edit", ("assistant", "12"): "owner"},
        direct={("assistant", "11"): {"editor"}},
    )
    response = await grants_api.list_resource_grants(account.id, login_user=_Admin())
    by_id = {r["resource_id"]: r for r in response.data["data"]}
    assert by_id["11"]["level"] == "editor"  # can_edit -> editor
    assert by_id["11"]["source"] == "direct"
    # owner 档不是代管关系:档位不映射,来源也不是 direct(撤不了)。
    assert by_id["12"]["level"] is None
    assert by_id["12"]["source"] == "inherited"


async def test_mutate_grant_uses_owner_as_subject(patched_db, fake_redis, account, monkeypatch):
    calls = _stub_permission(monkeypatch, admin_can_manage=True)

    response = await grants_api.mutate_resource_grants(
        account.id,
        grants_api.GrantMutateReq(
            grants=[grants_api.GrantItem(resource_type="assistant", resource_id="11", level="editor")]
        ),
        login_user=_Admin(),
    )
    assert response.data["granted"] == 1

    written = calls["authorize"][0]
    assert written["object"] == "assistant:11"
    item = written["grants"][0]
    # 主体恒为专用身份,relation=请求档位;前端不需要知道 subject 的存在
    assert item.subject_type == "user" and item.subject_id == account.resource_owner_user_id
    assert item.relation == "editor"


async def test_mutate_rejects_when_admin_cannot_manage_resource(patched_db, fake_redis, account, monkeypatch):
    calls = _stub_permission(monkeypatch, admin_can_manage=False)
    with pytest.raises(OpenApiResourceNotGrantedError):
        await grants_api.mutate_resource_grants(
            account.id,
            grants_api.GrantMutateReq(
                grants=[grants_api.GrantItem(resource_type="assistant", resource_id="11", level="viewer")]
            ),
            login_user=_Admin(),
        )
    assert calls["authorize"] == [], "越界时不得写任何 tuple"


async def test_mutate_rejects_unsupported_type_or_level(patched_db, fake_redis, account, monkeypatch):
    _stub_permission(monkeypatch, admin_can_manage=True)
    with pytest.raises(OpenApiResourceTypeUnsupportedError):
        await grants_api.mutate_resource_grants(
            account.id,
            grants_api.GrantMutateReq(
                grants=[grants_api.GrantItem(resource_type="flow_secret", resource_id="1", level="viewer")]
            ),
            login_user=_Admin(),
        )
    with pytest.raises(OpenApiResourceTypeUnsupportedError):
        await grants_api.mutate_resource_grants(
            account.id,
            grants_api.GrantMutateReq(
                grants=[grants_api.GrantItem(resource_type="assistant", resource_id="1", level="owner")]
            ),
            login_user=_Admin(),
        )


async def test_revoke_unknown_grant_is_404(patched_db, fake_redis, account, monkeypatch):
    _stub_permission(monkeypatch, admin_can_manage=True, levels={})
    with pytest.raises(OpenApiNotFoundError):
        await grants_api.mutate_resource_grants(
            account.id,
            grants_api.GrantMutateReq(revokes=[grants_api.RevokeItem(resource_type="assistant", resource_id="11")]),
            login_user=_Admin(),
        )


async def test_revoke_existing_grant_writes_revoke(patched_db, fake_redis, account, monkeypatch):
    calls = _stub_permission(
        monkeypatch,
        admin_can_manage=True,
        levels={("assistant", "11"): "viewer"},
        # 撤销的对象是**直接元组**:引擎档位(can_read)不能让撤销找错关系,
        # 真正写进去的关系取自直接元组本身。
        direct={("assistant", "11"): {"viewer"}},
    )
    await grants_api.mutate_resource_grants(
        account.id,
        grants_api.GrantMutateReq(revokes=[grants_api.RevokeItem(resource_type="assistant", resource_id="11")]),
        login_user=_Admin(),
    )
    revoke_item = calls["authorize"][0]["revokes"][0]
    assert revoke_item.subject_id == account.resource_owner_user_id and revoke_item.relation == "viewer"


async def test_revoke_clears_every_granted_relation(patched_db, fake_redis, account, monkeypatch):
    """先授 viewer 再升 editor 会并存两条元组:撤销要一次收干净,否则残留 viewer。"""
    calls = _stub_permission(
        monkeypatch,
        admin_can_manage=True,
        direct={("assistant", "11"): {"viewer", "editor"}},
    )
    response = await grants_api.mutate_resource_grants(
        account.id,
        grants_api.GrantMutateReq(revokes=[grants_api.RevokeItem(resource_type="assistant", resource_id="11")]),
        login_user=_Admin(),
    )
    assert response.data["revoked"] == 1  # 契约:计数 = 请求条目数
    relations = sorted(r.relation for call in calls["authorize"] for r in (call["revokes"] or []))
    assert relations == ["editor", "viewer"]  # 两条元组各撤一次


async def test_grant_downgrade_revokes_the_higher_relation(patched_db, fake_redis, account, monkeypatch):
    """把 editor 调成 viewer:必须顺带撤掉 editor,否则账号实际权限高于界面所示。"""
    calls = _stub_permission(monkeypatch, admin_can_manage=True, direct={("assistant", "11"): {"editor"}})
    await grants_api.mutate_resource_grants(
        account.id,
        grants_api.GrantMutateReq(
            grants=[grants_api.GrantItem(resource_type="assistant", resource_id="11", level="viewer")]
        ),
        login_user=_Admin(),
    )
    assert calls["authorize"][0]["grants"][0].relation == "viewer"
    revoked = [r.relation for call in calls["authorize"] for r in (call["revokes"] or [])]
    assert revoked == ["editor"]


async def test_fga_outage_surfaces_as_11814(patched_db, fake_redis, account, monkeypatch):
    """FGA 不可达 -> 11814/503;不允许把"读不到"静默当成"没有授权"。"""
    # 要有可见资源才会走到"读直接元组"那一步(空列表直接跳过)。
    _stub_permission(monkeypatch, admin_can_manage=True, grants_by_type={"assistant": [11]})

    async def _boom(user_id, object_type, object_id):
        raise FGAConnectionError("openfga down")

    monkeypatch.setattr(PermissionService, "list_direct_relations", _boom)
    with pytest.raises(OpenApiResourceGrantsUnavailableError):
        await grants_api.list_resource_grants(account.id, login_user=_Admin())


async def test_revoke_inherited_visibility_is_404(patched_db, fake_redis, account, monkeypatch):
    """继承来的可见性(部门/租户)不归代管管理:没有可撤的直接元组 -> 11809,且不写任何东西。"""
    calls = _stub_permission(
        monkeypatch,
        admin_can_manage=True,
        levels={("assistant", "11"): "can_read"},
        direct={},
    )
    with pytest.raises(OpenApiNotFoundError):
        await grants_api.mutate_resource_grants(
            account.id,
            grants_api.GrantMutateReq(revokes=[grants_api.RevokeItem(resource_type="assistant", resource_id="11")]),
            login_user=_Admin(),
        )
    assert calls["authorize"] == []


async def test_cross_tenant_account_is_404(patched_db, fake_redis, account, monkeypatch):
    _stub_permission(monkeypatch, admin_can_manage=True)
    with pytest.raises(OpenApiNotFoundError):
        await grants_api.list_resource_grants(account.id, login_user=_Admin(tenant_id=OTHER_TENANT))


# ---------------------------------------------------------------------------
# AC-41 静态断言
# ---------------------------------------------------------------------------


def test_t22_endpoints_declare_admin_dependency():
    for module in (grants_api, settings_api):
        source = pathlib.Path(module.__file__).read_text(encoding="utf-8")
        assert source.count("Depends(UserPayload.get_tenant_admin_user)") == source.count("@router."), (
            f"{module.__name__} 有端点漏声明管理依赖"
        )
