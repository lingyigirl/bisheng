"""F076 T-20:服务账号管理 API 的契约测试。

直接调用端点函数(依赖注入由 FastAPI 负责,测试里显式传桩管理员),验证:
- 形状与分页(design §4.2 / PageData);
- 跨租户一律 404/11809;重名 11813;换 owner 防提权 11810(复用 T-17 的判定);
- 删除后密钥全失效(spec AC-06);
- 全部端点上声明了 `get_tenant_admin_user`(spec AC-41 的非管理员拒绝由该房屋依赖保证)。
"""

import pathlib
from contextlib import asynccontextmanager

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import StaticPool

from bisheng.common.errcode.open_api import (
    OpenApiAccountNameDuplicateError,
    OpenApiKeyRevokedError,
    OpenApiNotFoundError,
    OpenApiOwnerNotAllowedError,
)
from bisheng.database.models.tenant import UserTenant
from bisheng.open_api.api.endpoints import service_account as api
from bisheng.open_api.domain.models.api_credential import ApiCredential
from bisheng.open_api.domain.models.service_account import ServiceAccount
from bisheng.open_api.domain.services import credential_service as cred_service
from bisheng.open_api.domain.services import credential_validator as validator
from bisheng.open_api.domain.services import identity_service
from bisheng.user.domain.models.user import User

TABLES = [User.__table__, UserTenant.__table__, ServiceAccount.__table__, ApiCredential.__table__]
TENANT_ID = 51
OTHER_TENANT = 52


class _Admin:
    """桩管理员:端点只读 user_id / tenant_id。"""

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
async def api_engine():
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
async def patched_db(api_engine, monkeypatch):
    @asynccontextmanager
    async def _factory():
        async with AsyncSession(bind=api_engine, expire_on_commit=False) as session:
            yield session

    monkeypatch.setattr("bisheng.open_api.domain.models.dao.get_async_db_session", _factory)
    monkeypatch.setattr("bisheng.open_api.domain.services.service_account_service.get_async_db_session", _factory)
    yield api_engine


@pytest.fixture()
def fake_redis(monkeypatch):
    fake = _FakeRedis()

    async def _get():
        return fake

    monkeypatch.setattr(cred_service, "_get_redis", _get)
    monkeypatch.setattr(cred_service, "_credential_cache_ttl", lambda: 3)
    return fake


def _stub_identity_reads(monkeypatch, *, privileged: bool = False):
    class _Payload:
        is_global_super = privileged

        async def has_tenant_admin(self, tenant_id: int) -> bool:
            return privileged

    async def _get_user(user_id: int):
        row = User(user_name=f"u{user_id}", password="x", source="local", external_id=None, delete=0)
        row.user_id = user_id
        return row

    async def _active_tenant(user_id: int):
        class _Row:
            tenant_id = TENANT_ID
            status = "active"

        return _Row()

    async def _init(user_id: int, user_name: str, tenant_id: int | None = None, **kwargs) -> _Payload:
        return _Payload()

    monkeypatch.setattr(identity_service.UserDao, "aget_user", _get_user)
    monkeypatch.setattr(identity_service.UserTenantDao, "aget_active_user_tenant", _active_tenant)
    monkeypatch.setattr(identity_service.UserPayload, "init_login_user", _init)


# ---------------------------------------------------------------------------
# 形状与分页
# ---------------------------------------------------------------------------


async def test_create_returns_design_shape(patched_db, monkeypatch):
    _stub_identity_reads(monkeypatch)
    response = await api.create_service_account(
        api.ServiceAccountCreateReq(name="信贷系统", description="对接"), login_user=_Admin()
    )
    data = response.data
    assert data == {
        "id": 1,
        "name": "信贷系统",
        "description": "对接",
        "resource_owner_user_id": data["resource_owner_user_id"],
        "enabled": True,
        "key_count": 0,
        "last_used_at": None,
    }
    assert data["resource_owner_user_id"]


async def test_list_paginates_and_filters(patched_db, fake_redis, monkeypatch):
    _stub_identity_reads(monkeypatch)
    for name in ("对接甲", "对接乙", "信贷丙"):
        await api.create_service_account(api.ServiceAccountCreateReq(name=name), login_user=_Admin())
    # 给第一个账号配一把密钥,验证 key_count
    account = await api.get_service_account(1, login_user=_Admin())
    await cred_service.issue_credential(tenant_id=TENANT_ID, service_account_id=account.data["id"], scopes=[])

    response = await api.list_service_accounts(keyword="对接", page=1, page_size=10, login_user=_Admin())
    assert response.data.total == 2
    assert {row["name"] for row in response.data.data} == {"对接甲", "对接乙"}

    page2 = await api.list_service_accounts(keyword=None, page=2, page_size=2, login_user=_Admin())
    assert page2.data.total == 3 and len(page2.data.data) == 1

    detail = await api.get_service_account(1, login_user=_Admin())
    assert detail.data["key_count"] == 1


# ---------------------------------------------------------------------------
# 跨租户一律 404 / 重名 11813 / 防提权 11810
# ---------------------------------------------------------------------------


async def test_cross_tenant_is_404(patched_db, fake_redis, monkeypatch):
    _stub_identity_reads(monkeypatch)
    await api.create_service_account(api.ServiceAccountCreateReq(name="对接系统"), login_user=_Admin())

    with pytest.raises(OpenApiNotFoundError):
        await api.get_service_account(1, login_user=_Admin(tenant_id=OTHER_TENANT))
    with pytest.raises(OpenApiNotFoundError):
        await api.delete_service_account(1, login_user=_Admin(tenant_id=OTHER_TENANT))
    with pytest.raises(OpenApiNotFoundError):
        await api.disable_service_account(1, login_user=_Admin(tenant_id=OTHER_TENANT))

    listing = await api.list_service_accounts(
        keyword=None, page=1, page_size=10, login_user=_Admin(tenant_id=OTHER_TENANT)
    )
    assert listing.data.total == 0


async def test_duplicate_name_and_owner_escalation(patched_db, fake_redis, monkeypatch):
    _stub_identity_reads(monkeypatch)
    await api.create_service_account(api.ServiceAccountCreateReq(name="对接系统"), login_user=_Admin())

    with pytest.raises(OpenApiAccountNameDuplicateError):
        await api.create_service_account(api.ServiceAccountCreateReq(name="对接系统"), login_user=_Admin())

    # 换 owner 到管理员 → 11810(第一重防提权,复用 T-17 的 assert_owner_assignable)
    _stub_identity_reads(monkeypatch, privileged=True)
    with pytest.raises(OpenApiOwnerNotAllowedError):
        await api.update_service_account(
            1, api.ServiceAccountUpdateReq(resource_owner_user_id=999), login_user=_Admin()
        )


async def test_rename_conflict_and_success(patched_db, fake_redis, monkeypatch):
    _stub_identity_reads(monkeypatch)
    await api.create_service_account(api.ServiceAccountCreateReq(name="甲"), login_user=_Admin())
    await api.create_service_account(api.ServiceAccountCreateReq(name="乙"), login_user=_Admin())

    with pytest.raises(OpenApiAccountNameDuplicateError):
        await api.update_service_account(1, api.ServiceAccountUpdateReq(name="乙"), login_user=_Admin())

    response = await api.update_service_account(1, api.ServiceAccountUpdateReq(name="甲改"), login_user=_Admin())
    assert response.data["name"] == "甲改"


# ---------------------------------------------------------------------------
# 生命周期:停用/删除后密钥全失效(spec AC-05 / AC-06)
# ---------------------------------------------------------------------------


async def test_disable_and_delete_revoke_keys(patched_db, fake_redis, monkeypatch):
    _stub_identity_reads(monkeypatch)
    await api.create_service_account(api.ServiceAccountCreateReq(name="对接系统"), login_user=_Admin())
    _, key1 = await cred_service.issue_credential(tenant_id=TENANT_ID, service_account_id=1, scopes=[])

    await api.disable_service_account(1, login_user=_Admin())
    with pytest.raises(OpenApiKeyRevokedError):
        await validator.validate_bearer(f"Bearer {key1}")

    await api.enable_service_account(1, login_user=_Admin())
    _, key2 = await cred_service.issue_credential(tenant_id=TENANT_ID, service_account_id=1, scopes=[])
    await api.delete_service_account(1, login_user=_Admin())
    with pytest.raises(OpenApiKeyRevokedError):
        await validator.validate_bearer(f"Bearer {key2}")

    # 软删后管理面不可见,且名字不复用(design 表1)
    with pytest.raises(OpenApiNotFoundError):
        await api.get_service_account(1, login_user=_Admin())
    with pytest.raises(OpenApiAccountNameDuplicateError):
        await api.create_service_account(api.ServiceAccountCreateReq(name="对接系统"), login_user=_Admin())


# ---------------------------------------------------------------------------
# AC-41:非管理员拒绝由房屋依赖保证 —— 每个端点都必须声明它
# ---------------------------------------------------------------------------


def test_every_management_endpoint_declares_admin_dependency():
    source = pathlib.Path(api.__file__).read_text(encoding="utf-8")
    route_count = source.count("@router.")
    declared = source.count("Depends(UserPayload.get_tenant_admin_user)")
    # 每个路由端点都要声明管理依赖;数量对不上 = 新增端点漏了声明的鉴权
    assert declared == route_count, f"routes={route_count} declared={declared}"
