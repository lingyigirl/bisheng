"""F076 T-05:密钥签发 / 校验 / 编辑 / 吊销 + 正缓存的单元测试。

覆盖 spec AC-08 / AC-09 / AC-10 / AC-11 / AC-12 / AC-13 / AC-14 / AC-17 / AC-18
与 design §3 决策 5(SHA-256 摘要、明文只出现一次)、§2 约束(缓存降级不阻塞)。

测试姿态:SQLite 异步引擎 + 把 DAO 模块的 `get_async_db_session` 换成测试会话工厂
(与 test/approval 同款);Redis 用进程内假实现注入 `credential_service._get_redis`,
既验证"缓存命中减少库读",也验证"Redis 不可用降级为查库"。
"""

from contextlib import asynccontextmanager
from datetime import datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import StaticPool
from sqlmodel import select

from bisheng.common.errcode.open_api import (
    OpenApiKeyExpiredError,
    OpenApiKeyInvalidError,
    OpenApiKeyMissingError,
    OpenApiKeyRevokedError,
)
from bisheng.open_api.domain.models.api_credential import (
    REVOKE_REASON_BATCH,
    REVOKE_REASON_MANUAL,
    ApiCredential,
)
from bisheng.open_api.domain.models.dao import ApiCredentialDao
from bisheng.open_api.domain.models.service_account import ServiceAccount
from bisheng.open_api.domain.services import credential_service as service
from bisheng.open_api.domain.services import credential_validator as validator

TABLES = [ServiceAccount.__table__, ApiCredential.__table__]
TENANT_ID = 11


class _FakeRedis:
    """进程内假 Redis:只实现本模块用到的 aset/aget/adelete(值的 pickle 语义无关紧要)。"""

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
async def oc_engine():
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
async def patched_db(oc_engine, monkeypatch):
    @asynccontextmanager
    async def _factory():
        async with AsyncSession(bind=oc_engine) as session:
            yield session

    monkeypatch.setattr("bisheng.open_api.domain.models.dao.get_async_db_session", _factory)
    yield oc_engine


@pytest.fixture()
def fake_redis(monkeypatch):
    fake = _FakeRedis()
    monkeypatch.setattr(service, "_get_redis", _async_return(fake))
    monkeypatch.setattr(service, "_credential_cache_ttl", lambda: 3)
    return fake


def _async_return(value):
    async def _inner():
        return value

    return _inner


async def _issue(**overrides):
    params = {
        "tenant_id": TENANT_ID,
        "service_account_id": 1,
        "name": "测试密钥",
        "scopes": ["workflow:invoke"],
    }
    params.update(overrides)
    return await service.issue_credential(**params)


# ---------------------------------------------------------------------------
# 签发(spec AC-08 / AC-09)
# ---------------------------------------------------------------------------


async def test_issue_shapes_and_hash_only(patched_db, fake_redis):
    row, plaintext = await _issue()

    assert plaintext.startswith("bs-sak-")
    assert len(plaintext) == len("bs-sak-") + 43
    assert row.token_hash == service.hash_token(plaintext)
    assert row.last4 == plaintext[-4:]
    assert row.key_mask == f"bs-sak-********{plaintext[-4:]}"
    # 明文不落库:行对象上没有明文属性,序列化里也不含明文
    assert not hasattr(row, "plaintext")
    assert plaintext not in row.model_dump_json()


async def test_issue_two_keys_coexist(patched_db, fake_redis):
    """同账号多把并存(spec AC-10):摘要不同、都能校验通过。"""
    row1, key1 = await _issue(name="k1")
    row2, key2 = await _issue(name="k2")
    assert row1.token_hash != row2.token_hash
    assert (await validator.validate_bearer(f"Bearer {key1}")).id == row1.id
    assert (await validator.validate_bearer(f"Bearer {key2}")).id == row2.id


# ---------------------------------------------------------------------------
# 校验失败语义(spec AC-17 / AC-18)
# ---------------------------------------------------------------------------


async def test_missing_or_malformed_key(patched_db, fake_redis):
    for header in [
        None,
        "",
        "Bearer",
        "Bearer not-a-key",
        f"Token bs-sak-{'a' * 43}",
        f"Bearer bs-pat-{'a' * 43}",
        f"Bearer bs-sak-{'a' * 42}",
    ]:
        with pytest.raises(OpenApiKeyMissingError):
            await validator.validate_bearer(header)


async def test_unknown_key(patched_db, fake_redis):
    with pytest.raises(OpenApiKeyInvalidError):
        await validator.validate_bearer(f"Bearer bs-sak-{'A' * 43}")


async def test_revoked_key(patched_db, fake_redis):
    row, plaintext = await _issue()
    assert await service.revoke_credential(row.id, tenant_id=TENANT_ID) is True
    with pytest.raises(OpenApiKeyRevokedError):
        await validator.validate_bearer(f"Bearer {plaintext}")


async def test_expired_key_and_boundary(patched_db, fake_redis):
    now = datetime(2030, 1, 1, 12, 0, 0)
    _, expired = await _issue(name="expired", expires_at=now - timedelta(seconds=1))
    _, exactly_now = await _issue(name="boundary", expires_at=now)
    _, future = await _issue(name="future", expires_at=now + timedelta(seconds=1))

    with pytest.raises(OpenApiKeyExpiredError):
        await validator.validate_bearer(f"Bearer {expired}", now=now)
    # 过期判定排他:到期时刻本身即无效(spec AC-12)
    with pytest.raises(OpenApiKeyExpiredError):
        await validator.validate_bearer(f"Bearer {exactly_now}", now=now)
    assert (await validator.validate_bearer(f"Bearer {future}", now=now)).name == "future"


# ---------------------------------------------------------------------------
# 缓存行为(spec AC-11 / AC-14 的时延口径)
# ---------------------------------------------------------------------------


async def test_cache_hit_skips_db_and_revoke_invalidates(patched_db, fake_redis, monkeypatch):
    row, plaintext = await _issue()

    calls = {"n": 0}
    orig = ApiCredentialDao.aget_by_hash

    async def counting(token_hash):
        calls["n"] += 1
        return await orig(token_hash)

    monkeypatch.setattr(ApiCredentialDao, "aget_by_hash", counting)

    await validator.validate_bearer(f"Bearer {plaintext}")
    assert calls["n"] == 1  # 首次查库并回填
    await validator.validate_bearer(f"Bearer {plaintext}")
    assert calls["n"] == 1  # 命中缓存,不再查库

    # 吊销后清缓存 → 下一请求立即按新状态判定,无需等 TTL
    await service.revoke_credential(row.id, tenant_id=TENANT_ID)
    assert not fake_redis.store, "revoke must purge the cached credential"
    with pytest.raises(OpenApiKeyRevokedError):
        await validator.validate_bearer(f"Bearer {plaintext}")


async def test_redis_down_degrades_to_db(patched_db, monkeypatch):
    """Redis 不可用 = 缓存直通,不是失败(缓存是优化不是权威)。"""
    monkeypatch.setattr(service, "_get_redis", _async_return(None))
    row, plaintext = await _issue()
    assert (await validator.validate_bearer(f"Bearer {plaintext}")).id == row.id


# ---------------------------------------------------------------------------
# 编辑与全量吊销(spec AC-11 / AC-13)
# ---------------------------------------------------------------------------


async def test_update_whitelist_and_effect(patched_db, fake_redis):
    row, plaintext = await _issue(name="old", scopes=["workflow:invoke"])

    assert await service.update_credential(row.id, {"name": "new", "scopes": ["knowledge:read"]}, tenant_id=TENANT_ID)
    refreshed = await validator.validate_bearer(f"Bearer {plaintext}")
    assert refreshed.name == "new"
    assert refreshed.scopes == ["knowledge:read"]

    with pytest.raises(ValueError):
        await service.update_credential(row.id, {"token_hash": "x"}, tenant_id=TENANT_ID)


async def test_revoke_account_credentials_bulk(patched_db, fake_redis):
    _row1, key1 = await _issue(name="k1")
    _row2, key2 = await _issue(name="k2")
    pref_revoked, key3 = await _issue(name="k3")
    await service.revoke_credential(pref_revoked.id, tenant_id=TENANT_ID, reason=REVOKE_REASON_MANUAL)

    changed = await service.revoke_account_credentials(1, tenant_id=TENANT_ID)
    assert changed == 2  # 只数未吊销的两把

    for key in (key1, key2, key3):
        with pytest.raises(OpenApiKeyRevokedError):
            await validator.validate_bearer(f"Bearer {key}")

    # 吊销 ≠ 删除:记录保留(spec AC-11)
    async with AsyncSession(bind=patched_db) as session:
        rows = (await session.execute(select(ApiCredential))).all()
    assert len(rows) == 3
    reasons = {r[0].revoke_reason for r in rows}
    assert reasons == {REVOKE_REASON_MANUAL, REVOKE_REASON_BATCH}
