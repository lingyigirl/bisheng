"""F076 T-21:密钥管理 API 契约测试。

验证:签发**明文仅一次**(此后一律掩码)、权限位目录校验(11819)、停用账号拒签
(11805)、跨租户 404、编辑立即生效、单把/全量吊销即时失效、权限位目录内容。
"""

import pathlib
from contextlib import asynccontextmanager

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import StaticPool

from bisheng.common.errcode.open_api import (
    OpenApiAccountDisabledError,
    OpenApiKeyRevokedError,
    OpenApiNotFoundError,
    OpenApiUnknownScopeError,
)
from bisheng.database.models.tenant import UserTenant
from bisheng.open_api.api.endpoints import service_account as sa_api
from bisheng.open_api.api.endpoints import service_account_keys as keys_api
from bisheng.open_api.domain.models.api_credential import ApiCredential
from bisheng.open_api.domain.models.service_account import ServiceAccount
from bisheng.open_api.domain.services import credential_service as cred_service
from bisheng.open_api.domain.services import credential_validator as validator
from bisheng.open_api.domain.services import identity_service
from bisheng.open_api.domain.services import service_account_service as svc
from bisheng.user.domain.models.user import User

TABLES = [User.__table__, UserTenant.__table__, ServiceAccount.__table__, ApiCredential.__table__]
TENANT_ID = 61
OTHER_TENANT = 62


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
async def k_engine():
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
async def patched_db(k_engine, monkeypatch):
    @asynccontextmanager
    async def _factory():
        async with AsyncSession(bind=k_engine, expire_on_commit=False) as session:
            yield session

    monkeypatch.setattr("bisheng.open_api.domain.models.dao.get_async_db_session", _factory)
    monkeypatch.setattr("bisheng.open_api.domain.services.service_account_service.get_async_db_session", _factory)
    yield k_engine


@pytest.fixture()
def fake_redis(monkeypatch):
    fake = _FakeRedis()

    async def _get():
        return fake

    monkeypatch.setattr(cred_service, "_get_redis", _get)
    monkeypatch.setattr(cred_service, "_credential_cache_ttl", lambda: 3)
    return fake


@pytest.fixture()
async def account(patched_db, monkeypatch):
    """预置一个服务账号(建号路径已由 T-17/T-20 覆盖,这里直用服务)。"""
    monkeypatch.setattr(identity_service.UserPayload, "init_login_user", _stub_init)
    return await svc.create_service_account(tenant_id=TENANT_ID, name="对接系统")


async def _stub_init(user_id: int, user_name: str, tenant_id: int | None = None, **kwargs):
    class _P:
        is_global_super = False

        async def has_tenant_admin(self, tenant_id: int) -> bool:
            return False

    return _P()


async def test_issue_plaintext_once_then_masked(patched_db, fake_redis, account):
    issued = await keys_api.issue_key(
        account.id,
        keys_api.KeyIssueReq(name="生产密钥", scopes=["assistant:read", "workflow:invoke"]),
        login_user=_Admin(),
    )
    data = issued.data
    assert data["plaintext"].startswith("bs-sak-")
    assert data["key_mask"].endswith(data["plaintext"][-4:])
    assert data["plaintext"] not in str(data["key_mask"])

    listed = await keys_api.list_keys(account.id, login_user=_Admin())
    row = listed.data.data[0]
    assert "plaintext" not in row
    assert row["key_mask"] == data["key_mask"]
    assert row["status"] == "active"
    assert row["scopes"] == ["assistant:read", "workflow:invoke"]

    # 明文可用,且校验通过
    assert (await validator.validate_bearer(f"Bearer {data['plaintext']}")).id == data["id"]


async def test_issue_rejects_unknown_scope_and_disabled_account(patched_db, fake_redis, account):
    with pytest.raises(OpenApiUnknownScopeError) as exc:
        await keys_api.issue_key(account.id, keys_api.KeyIssueReq(scopes=["not:a:scope"]), login_user=_Admin())
    assert exc.value.kwargs.get("unknown") == ["not:a:scope"]

    await svc.set_enabled(account.id, enabled=False, tenant_id=TENANT_ID)
    with pytest.raises(OpenApiAccountDisabledError):
        await keys_api.issue_key(account.id, keys_api.KeyIssueReq(scopes=[]), login_user=_Admin())


async def test_cross_tenant_is_404(patched_db, fake_redis, account):
    with pytest.raises(OpenApiNotFoundError):
        await keys_api.list_keys(account.id, login_user=_Admin(tenant_id=OTHER_TENANT))
    with pytest.raises(OpenApiNotFoundError):
        await keys_api.issue_key(account.id, keys_api.KeyIssueReq(scopes=[]), login_user=_Admin(tenant_id=OTHER_TENANT))


async def test_update_takes_effect_immediately(patched_db, fake_redis, account):
    issued = await keys_api.issue_key(account.id, keys_api.KeyIssueReq(scopes=["assistant:read"]), login_user=_Admin())
    key_id, plaintext = issued.data["id"], issued.data["plaintext"]

    updated = await keys_api.update_key(
        account.id,
        key_id,
        keys_api.KeyUpdateReq(name="改名", scopes=["workflow:invoke"]),
        login_user=_Admin(),
    )
    assert updated.data["name"] == "改名"
    assert updated.data["scopes"] == ["workflow:invoke"]

    # 权限位即时生效:校验器读到的就是新 scope(缓存已清)
    row = await validator.validate_bearer(f"Bearer {plaintext}")
    assert row.scopes == ["workflow:invoke"]

    with pytest.raises(OpenApiUnknownScopeError):
        await keys_api.update_key(account.id, key_id, keys_api.KeyUpdateReq(scopes=["nope"]), login_user=_Admin())


async def test_revoke_single_and_all(patched_db, fake_redis, account):
    k1 = await keys_api.issue_key(account.id, keys_api.KeyIssueReq(scopes=[]), login_user=_Admin())
    k2 = await keys_api.issue_key(account.id, keys_api.KeyIssueReq(scopes=[]), login_user=_Admin())

    await keys_api.revoke_key(account.id, k1.data["id"], login_user=_Admin())
    with pytest.raises(OpenApiKeyRevokedError):
        await validator.validate_bearer(f"Bearer {k1.data['plaintext']}")
    # 另一把不受影响
    assert (await validator.validate_bearer(f"Bearer {k2.data['plaintext']}")).id == k2.data["id"]

    revoked = await keys_api.revoke_all_keys(account.id, login_user=_Admin())
    assert revoked.data["revoked"] == 1  # 只数未吊销的
    with pytest.raises(OpenApiKeyRevokedError):
        await validator.validate_bearer(f"Bearer {k2.data['plaintext']}")

    # 吊销 ≠ 删除:列表仍能看到,状态为 revoked
    listed = await keys_api.list_keys(account.id, login_user=_Admin())
    assert {row["status"] for row in listed.data.data} == {"revoked"}


async def test_scopes_catalog_lists_eight_issuable(patched_db):
    catalog = await keys_api.list_scopes(login_user=_Admin())
    codes = {item["code"] for item in catalog.data["data"]}
    assert codes == {
        "workflow:invoke",
        "workflow:read",
        "assistant:invoke",
        "assistant:read",
        "chat:invoke",
        "knowledge:read",
        "knowledge:write",
        "delegate",
    }
    assert catalog.data["total"] == 8


def test_every_key_endpoint_declares_admin_dependency():
    for module in (keys_api, sa_api):
        source = pathlib.Path(module.__file__).read_text(encoding="utf-8")
        assert source.count("Depends(UserPayload.get_tenant_admin_user)") == source.count("@router.") + source.count(
            "@scopes_router."
        ), f"{module.__name__} 有端点漏声明管理依赖"
