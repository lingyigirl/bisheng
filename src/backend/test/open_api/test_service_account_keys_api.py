"""F076 T-21:密钥管理 API 契约测试。

验证:签发返回明文、列表一律掩码、管理员经 reveal 查看(审计)、权限位目录校验(11819)、停用账号拒签
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


async def test_scopes_catalog_lists_all_issuable(patched_db):
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
        "schedule:trigger",
        "managed_agent:invoke",
        "delegate",
    }
    assert catalog.data["total"] == 10


def test_every_key_endpoint_declares_admin_dependency():
    for module in (keys_api, sa_api):
        source = pathlib.Path(module.__file__).read_text(encoding="utf-8")
        assert source.count("Depends(UserPayload.get_tenant_admin_user)") == source.count("@router.") + source.count(
            "@scopes_router."
        ), f"{module.__name__} 有端点漏声明管理依赖"


# ---------------------------------------------------------------------------
# 2026-10-22 增量:明文查看 + 每 Key 智能体白名单
# ---------------------------------------------------------------------------


async def test_reveal_returns_plaintext_and_legacy_is_11823(patched_db, fake_redis, account):

    from fastapi import Response

    from bisheng.common.errcode.open_api import OpenApiKeyNotRevealableError

    issued = await keys_api.issue_key(
        account.id,
        keys_api.KeyIssueReq(name="可查看", scopes=["assistant:read"]),
        login_user=_Admin(),
    )
    key_id = issued.data["id"]
    assert issued.data["plaintext"].startswith("bs-sak-")

    listed = await keys_api.list_keys(account.id, login_user=_Admin())
    listed_row = next(row for row in listed.data.data if row["id"] == key_id)
    assert listed_row["revealable"] is True
    assert listed_row["allowed_apps"] is None

    revealed = await keys_api.reveal_key(account.id, key_id, response=Response(), login_user=_Admin())
    assert revealed.data["plaintext"] == issued.data["plaintext"]
    assert revealed.data["key_mask"] == issued.data["key_mask"]

    # 模拟存量密钥(无查看密文):400/11823,不返回明文。
    async with AsyncSession(bind=patched_db) as session:
        row = await session.get(ApiCredential, key_id)
        row.token_ciphertext = None
        await session.commit()
    with pytest.raises(OpenApiKeyNotRevealableError):
        await keys_api.reveal_key(account.id, key_id, response=Response(), login_user=_Admin())


async def test_issue_with_allowed_apps(patched_db, fake_redis, account, monkeypatch):
    import uuid

    from bisheng.open_api.domain.services import identity_service as keys_identity
    from bisheng.permission.domain.services.application_permission_service import (
        ApplicationPermissionService,
    )

    async def _owner(*, owner_user_id, tenant_id):
        class _P:
            user_id = 99

        return _P()

    async def _visible(owner, object_type, object_id, permission_ids):
        return True

    monkeypatch.setattr(keys_identity, "load_owner_payload", _owner)
    monkeypatch.setattr(ApplicationPermissionService, "has_any_permission_async", _visible)

    assistant_id = uuid.uuid4().hex
    issued = await keys_api.issue_key(
        account.id,
        keys_api.KeyIssueReq(
            name="限定智能体",
            scopes=["assistant:invoke"],
            allowed_apps=[keys_api.AgentRef(type="assistant", id=assistant_id)],
        ),
        login_user=_Admin(),
    )
    assert issued.data["allowed_apps"] == [{"type": "assistant", "id": assistant_id}]

    listed = await keys_api.list_keys(account.id, login_user=_Admin())
    row = next(one for one in listed.data.data if one["id"] == issued.data["id"])
    assert row["allowed_apps"] == [{"type": "assistant", "id": assistant_id}]

    # 编辑:显式 null 清除限制;[] 保留为全拒绝。
    await keys_api.update_key(
        account.id,
        issued.data["id"],
        keys_api.KeyUpdateReq(allowed_apps=[]),
        login_user=_Admin(),
    )
    listed = await keys_api.list_keys(account.id, login_user=_Admin())
    row = next(one for one in listed.data.data if one["id"] == issued.data["id"])
    assert row["allowed_apps"] == []

    await keys_api.update_key(
        account.id,
        issued.data["id"],
        keys_api.KeyUpdateReq(allowed_apps=None),
        login_user=_Admin(),
    )
    listed = await keys_api.list_keys(account.id, login_user=_Admin())
    row = next(one for one in listed.data.data if one["id"] == issued.data["id"])
    assert row["allowed_apps"] is None


async def test_issue_rejects_malformed_agent_ref(patched_db, fake_redis, account):
    from bisheng.common.errcode.open_api import OpenApiAgentNotAssignableError

    with pytest.raises(OpenApiAgentNotAssignableError):
        await keys_api.issue_key(
            account.id,
            keys_api.KeyIssueReq(
                name="坏条目",
                scopes=["assistant:invoke"],
                allowed_apps=[keys_api.AgentRef(type="assistant", id="not-a-uuid")],
            ),
            login_user=_Admin(),
        )


def test_reveal_endpoint_http_shape_and_no_store(monkeypatch):
    """真实 FastAPI 注入:Response 头生效、路由形状正确(直接调用测不到这两点)。"""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from bisheng.common.dependencies.user_deps import UserPayload
    from bisheng.core.config.settings import encrypt_token

    plaintext = "bs-sak-" + "x" * 43
    row = ApiCredential(
        id=42,
        tenant_id=TENANT_ID,
        service_account_id=1,
        name="生产",
        key_prefix="bs-sak-",
        last4=plaintext[-4:],
        token_hash=cred_service.hash_token(plaintext),
        token_ciphertext=encrypt_token(plaintext).decode(),
        scopes=["assistant:read"],
        allowed_apps=None,
    )
    account = ServiceAccount(id=1, tenant_id=TENANT_ID, name="acct", resource_owner_user_id=99)

    async def _account(*args, **kwargs):
        return account

    async def _row(*args, **kwargs):
        return row

    monkeypatch.setattr(keys_api.svc, "get_service_account", _account)
    monkeypatch.setattr(keys_api.ApiCredentialDao, "aget_by_id", _row)

    app = FastAPI()
    app.include_router(keys_api.router)
    app.dependency_overrides[UserPayload.get_tenant_admin_user] = lambda: _Admin()
    client = TestClient(app)

    response = client.post("/service-accounts/1/keys/42/reveal")

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    body = response.json()
    assert body["data"]["plaintext"] == plaintext
    assert body["data"]["key_mask"] == row.key_mask
