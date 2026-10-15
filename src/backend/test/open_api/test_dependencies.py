"""F076 T-06:鉴权依赖(HTTP 分支)的契约测试。

直接驱动 `verify_open_api_access` 这个异步生成器,喂入真实的 starlette
Request/WebSocket(从 ASGI scope 构造),覆盖 spec AC-16~AC-25 的失败语义与
design 决策 10 的三种分流(总闸关 / 无名 / 带密钥)。

owner 装载被替换为桩(它要连 user/UserTenant 与 FGA,属 T-06 之外的加载面);
本文件专注依赖自身的判定顺序与上下文装载/复位。
"""

import contextlib
from contextlib import asynccontextmanager
from datetime import datetime, timedelta

import pytest
from fastapi import WebSocket, WebSocketException
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import StaticPool
from starlette.requests import Request

from bisheng.common.errcode.open_api import (
    OpenApiAccountDisabledError,
    OpenApiEndpointUnregisteredError,
    OpenApiIdentityInputRejectedError,
    OpenApiKeyInvalidError,
    OpenApiKeyMissingError,
    OpenApiKeyRevokedError,
    OpenApiOwnerPrivilegedError,
    OpenApiScopeMissingError,
)
from bisheng.core.config.open_api import OpenApiConf
from bisheng.core.context.tenant import DEFAULT_TENANT_ID, get_current_tenant_id, get_visible_tenant_ids
from bisheng.open_api.api import dependencies as deps
from bisheng.open_api.domain.context import get_open_api_principal
from bisheng.open_api.domain.models.api_credential import ApiCredential
from bisheng.open_api.domain.models.service_account import ServiceAccount
from bisheng.open_api.domain.scopes import open_api_scope
from bisheng.open_api.domain.services import credential_service as cred_service
from bisheng.open_api.domain.services import credential_validator as validator
from bisheng.open_api.domain.services import tenant_setting_service

TABLES = [ServiceAccount.__table__, ApiCredential.__table__]
TENANT_ID = 31
OWNER_ID = 9031


class _StubPayload:
    """UserPayload 的最小替身:只提供依赖用到的两个判定面。"""

    def __init__(self, *, global_super: bool = False, tenant_admin: bool = False):
        self.is_global_super = global_super
        self._tenant_admin = tenant_admin

    async def has_tenant_admin(self, tenant_id: int) -> bool:
        return self._tenant_admin


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
async def dep_engine():
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
async def patched_db(dep_engine, monkeypatch):
    @asynccontextmanager
    async def _factory():
        async with AsyncSession(bind=dep_engine) as session:
            yield session

    monkeypatch.setattr("bisheng.open_api.domain.models.dao.get_async_db_session", _factory)
    yield dep_engine


@pytest.fixture()
def fake_redis(monkeypatch):
    fake = _FakeRedis()

    async def _get():
        return fake

    monkeypatch.setattr(cred_service, "_get_redis", _get)
    monkeypatch.setattr(cred_service, "_credential_cache_ttl", lambda: 3)
    return fake


def _enable_deployment(monkeypatch, on: bool = True):
    # ConfigService 开启了 validate_assignment —— 必须赋真实的 OpenApiConf 实例。
    monkeypatch.setattr(deps.settings, "open_api", OpenApiConf(enforce_keys=on))


def _tenant_enforced(monkeypatch, mapping: dict[int, bool]):
    async def _is_enforced(tenant_id: int) -> bool:
        return mapping.get(tenant_id, False)

    monkeypatch.setattr(tenant_setting_service, "is_enforced", _is_enforced)


def _stub_owner(monkeypatch, payload=None, *, privileged: bool = False):
    async def _load(*, owner_user_id: int, tenant_id: int):
        if payload is None:
            return None
        return payload

    monkeypatch.setattr(deps, "load_owner_payload", _load)
    if privileged and payload is not None:
        payload.is_global_super = True


@open_api_scope("workflow:invoke")
def _marked_endpoint():
    return None


def _http(authorization: str | None = None, *, query: str = "", endpoint=_marked_endpoint):
    headers = []
    if authorization is not None:
        headers.append((b"authorization", authorization.encode()))
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/api/v2/workflow/invoke",
        "query_string": query.encode(),
        "headers": headers,
        "endpoint": endpoint,
    }
    return Request(scope)


def _ws(authorization: str | None = None, *, query: str = "", endpoint=_marked_endpoint):
    headers = []
    if authorization is not None:
        headers.append((b"authorization", authorization.encode()))
    scope = {
        "type": "websocket",
        "path": "/api/v2/workflow/chat/x",
        "query_string": query.encode(),
        "headers": headers,
        "endpoint": endpoint,
    }

    async def _receive():
        return {"type": "websocket.disconnect"}

    async def _send(message):
        return None

    return WebSocket(scope, receive=_receive, send=_send)


async def _enter(conn):
    """驱动依赖到 yield;失败会在这里抛出。"""
    gen = deps.verify_open_api_access(conn)
    await gen.__anext__()
    return gen


async def _finish(gen):
    """把生成器跑完;正常结束(StopAsyncIteration)视为通过。"""
    with contextlib.suppress(StopAsyncIteration):
        await gen.__anext__()


async def _seed_account_and_key(tenant_id=TENANT_ID, *, enabled=True, scopes=None):
    # 账号行直接经(已打补丁的)会话工厂插入:ServiceAccountDao 目前只有
    # aget_by_id(T-06 所需),管理面写入方法随 T-17/T-20 落地。
    from bisheng.open_api.domain.models import dao as dao_mod

    account = ServiceAccount(
        tenant_id=tenant_id,
        name=f"acct-{tenant_id}",
        resource_owner_user_id=OWNER_ID,
        disabled_at=None if enabled else datetime.now(),
    )
    async with dao_mod.get_async_db_session() as session:
        session.add(account)
        await session.commit()
        await session.refresh(account)
    _, plaintext = await cred_service.issue_credential(
        tenant_id=tenant_id,
        service_account_id=account.id,
        scopes=scopes if scopes is not None else ["workflow:invoke"],
    )
    return account, plaintext


# ---------------------------------------------------------------------------
# ①/② 三种分流(design 决策 10)
# ---------------------------------------------------------------------------


async def test_deployment_off_is_legacy_even_with_garbage_key(patched_db, fake_redis, monkeypatch):
    """总闸关 → 完全旧行为:乱写的 Authorization 也不拦,不装 principal(AC-21)。"""
    _enable_deployment(monkeypatch, False)
    gen = await _enter(_http("Bearer nonsense"))
    assert get_open_api_principal() is None
    await _finish(gen)


async def test_keyless_enforced_only_when_default_tenant_enabled(patched_db, fake_redis, monkeypatch):
    """无名请求按默认租户开关判定(决策 10 ②)。"""
    _enable_deployment(monkeypatch, True)

    _tenant_enforced(monkeypatch, {DEFAULT_TENANT_ID: False})
    gen = await _enter(_http())  # 租户未开启 → 旧行为
    assert get_open_api_principal() is None
    await _finish(gen)

    _tenant_enforced(monkeypatch, {DEFAULT_TENANT_ID: True})
    with pytest.raises(OpenApiKeyMissingError):
        await _enter(_http())


async def test_valid_key_installs_identity_and_rebuilds_tenant_context(patched_db, fake_redis, monkeypatch):
    """带密钥 → 永远按服务账号身份执行,且租户上下文被显式重建(决策 9)。"""
    _enable_deployment(monkeypatch, True)
    _tenant_enforced(monkeypatch, {TENANT_ID: True})
    owner = _StubPayload()
    _stub_owner(monkeypatch, owner)
    account, plaintext = await _seed_account_and_key()

    gen = await _enter(_http(f"Bearer {plaintext}"))

    principal = get_open_api_principal()
    assert principal is not None
    assert principal.tenant_id == TENANT_ID
    assert principal.service_account_id == account.id
    assert principal.owner_payload is owner
    assert get_current_tenant_id() == TENANT_ID
    assert get_visible_tenant_ids() == frozenset({DEFAULT_TENANT_ID, TENANT_ID})

    await _finish(gen)  # 结束 → finally 复位
    assert get_open_api_principal() is None
    assert get_current_tenant_id() != TENANT_ID or get_visible_tenant_ids() is None


async def test_keyed_request_honored_even_when_tenant_switch_off(patched_db, fake_redis, monkeypatch):
    """决策 10 ③:租户开关关不回落默认操作员——"带密钥反而提权"是退化,禁止。"""
    _enable_deployment(monkeypatch, True)
    _tenant_enforced(monkeypatch, {TENANT_ID: False})
    _stub_owner(monkeypatch, _StubPayload())
    _, plaintext = await _seed_account_and_key()

    gen = await _enter(_http(f"Bearer {plaintext}"))
    assert get_open_api_principal() is not None
    await _finish(gen)


# ---------------------------------------------------------------------------
# ③-⑦ 失败语义
# ---------------------------------------------------------------------------


async def test_invalid_and_revoked_keys(patched_db, fake_redis, monkeypatch):
    _enable_deployment(monkeypatch, True)
    _stub_owner(monkeypatch, _StubPayload())

    with pytest.raises(OpenApiKeyInvalidError):
        await _enter(_http(f"Bearer bs-sak-{'A' * 43}"))

    _account, plaintext = await _seed_account_and_key()
    await cred_service.revoke_credential(
        (await validator.validate_bearer(f"Bearer {plaintext}")).id, tenant_id=TENANT_ID
    )
    with pytest.raises(OpenApiKeyRevokedError):
        await _enter(_http(f"Bearer {plaintext}"))


async def test_disabled_account(patched_db, fake_redis, monkeypatch):
    _enable_deployment(monkeypatch, True)
    _stub_owner(monkeypatch, _StubPayload())
    _, plaintext = await _seed_account_and_key(enabled=False)

    with pytest.raises(OpenApiAccountDisabledError):
        await _enter(_http(f"Bearer {plaintext}"))


async def test_missing_marker_fails_closed(patched_db, fake_redis, monkeypatch):
    _enable_deployment(monkeypatch, True)
    _stub_owner(monkeypatch, _StubPayload())
    _, plaintext = await _seed_account_and_key()

    def _bare_endpoint():
        return None

    with pytest.raises(OpenApiEndpointUnregisteredError):
        await _enter(_http(f"Bearer {plaintext}", endpoint=_bare_endpoint))


async def test_scope_missing(patched_db, fake_redis, monkeypatch):
    _enable_deployment(monkeypatch, True)
    _stub_owner(monkeypatch, _StubPayload())
    _, plaintext = await _seed_account_and_key(scopes=["knowledge:read"])

    with pytest.raises(OpenApiScopeMissingError):
        await _enter(_http(f"Bearer {plaintext}"))


async def test_owner_privileged_is_refused(patched_db, fake_redis, monkeypatch):
    _enable_deployment(monkeypatch, True)
    _stub_owner(monkeypatch, _StubPayload(global_super=True))
    _, plaintext = await _seed_account_and_key()

    with pytest.raises(OpenApiOwnerPrivilegedError):
        await _enter(_http(f"Bearer {plaintext}"))


async def test_bare_user_id_rejected(patched_db, fake_redis, monkeypatch):
    _enable_deployment(monkeypatch, True)
    _stub_owner(monkeypatch, _StubPayload())
    _, plaintext = await _seed_account_and_key()

    with pytest.raises(OpenApiIdentityInputRejectedError):
        await _enter(_http(f"Bearer {plaintext}", query="user_id=1"))


# ---------------------------------------------------------------------------
# WS 分支:一律 WebSocketException(1008),绝不 HTTPException
# ---------------------------------------------------------------------------


async def test_ws_failures_raise_1008(patched_db, fake_redis, monkeypatch):
    _enable_deployment(monkeypatch, True)
    _tenant_enforced(monkeypatch, {DEFAULT_TENANT_ID: True})
    _stub_owner(monkeypatch, _StubPayload())

    with pytest.raises(WebSocketException) as exc_info:
        await _enter(_ws())  # 无名 + 默认租户强制
    assert exc_info.value.code == 1008

    with pytest.raises(WebSocketException) as exc_info:
        await _enter(_ws(f"Bearer bs-sak-{'B' * 43}"))  # 无效密钥
    assert exc_info.value.code == 1008


async def test_ws_valid_key_installs_principal(patched_db, fake_redis, monkeypatch):
    _enable_deployment(monkeypatch, True)
    _stub_owner(monkeypatch, _StubPayload())
    _, plaintext = await _seed_account_and_key()

    gen = await _enter(_ws(f"Bearer {plaintext}"))
    assert get_open_api_principal() is not None
    await _finish(gen)


async def test_expired_key_rejected(patched_db, fake_redis, monkeypatch):
    _enable_deployment(monkeypatch, True)
    _stub_owner(monkeypatch, _StubPayload())
    from bisheng.common.errcode.open_api import OpenApiKeyExpiredError

    _, plaintext = await cred_service.issue_credential(
        tenant_id=TENANT_ID,
        service_account_id=1,
        scopes=["workflow:invoke"],
        expires_at=datetime.now() - timedelta(seconds=1),
    )
    with pytest.raises(OpenApiKeyExpiredError):
        await _enter(_http(f"Bearer {plaintext}"))


# ---------------------------------------------------------------------------
# ②a 平台免登录面(T-14,discovery 决策 D5)
# ---------------------------------------------------------------------------

@open_api_scope("assistant:read", guest=True)
def _guest_endpoint():
    return None


def _stub_guest_switch(monkeypatch, on: bool):
    async def _enabled() -> bool:
        return on

    monkeypatch.setattr(deps, "_guest_access_enabled", _enabled)


async def test_guest_endpoint_allowed_when_switch_on(patched_db, fake_redis, monkeypatch):
    """免登录开关开:guest 端点匿名放行(旧行为),即便默认租户已强制。"""
    _enable_deployment(monkeypatch, True)
    _tenant_enforced(monkeypatch, {DEFAULT_TENANT_ID: True})
    _stub_guest_switch(monkeypatch, True)

    gen = await deps.verify_open_api_access(_http(endpoint=_guest_endpoint)).__anext__()  # noqa: F841
    assert get_open_api_principal() is None


async def test_guest_endpoint_rejected_when_switch_off(patched_db, fake_redis, monkeypatch):
    """免登录开关关:guest 端点与其余端点一视同仁(默认租户强制 → 11801)。"""
    _enable_deployment(monkeypatch, True)
    _tenant_enforced(monkeypatch, {DEFAULT_TENANT_ID: True})
    _stub_guest_switch(monkeypatch, False)

    with pytest.raises(OpenApiKeyMissingError):
        await _enter(_http(endpoint=_guest_endpoint))


async def test_non_guest_endpoint_ignores_guest_switch(patched_db, fake_redis, monkeypatch):
    """未标 guest 的端点不受免登录开关影响。"""
    _enable_deployment(monkeypatch, True)
    _tenant_enforced(monkeypatch, {DEFAULT_TENANT_ID: True})
    _stub_guest_switch(monkeypatch, True)

    with pytest.raises(OpenApiKeyMissingError):
        await _enter(_http())  # _marked_endpoint 无 guest 标记
