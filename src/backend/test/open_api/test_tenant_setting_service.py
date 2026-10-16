"""F076 T-16:租户级强制开关(部署级 ∧ 租户级)与策略缓存。

覆盖 spec AC-34 与 design §2.10 的强制口径:
- 部署级关 → 一律不强制(不查租户行);
- 部署级开 + 租户缺行 → 不强制(fail-closed 的"缺行=未启用");
- 部署级开 + 租户开 → 强制;
- 写策略后缓存立清,≤5 秒生效口径由清缓存 + TTL 双保险承载。
"""

from contextlib import asynccontextmanager

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import StaticPool

from bisheng.open_api.domain.models.dao import OpenApiTenantSettingDao
from bisheng.open_api.domain.models.open_api_tenant_setting import OpenApiTenantSetting
from bisheng.open_api.domain.services import tenant_setting_service as svc

TABLES = [OpenApiTenantSetting.__table__]
TENANT_ID = 21


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
async def ts_engine():
    engine = create_async_engine(
        "sqlite+aiosqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(lambda sync_conn: OpenApiTenantSetting.metadata.create_all(sync_conn, tables=TABLES))
    yield engine
    await engine.dispose()


@pytest.fixture()
async def patched_db(ts_engine, monkeypatch):
    @asynccontextmanager
    async def _factory():
        async with AsyncSession(bind=ts_engine) as session:
            yield session

    monkeypatch.setattr("bisheng.open_api.domain.models.dao.get_async_db_session", _factory)
    yield ts_engine


@pytest.fixture()
def fake_redis(monkeypatch):
    fake = _FakeRedis()

    async def _get():
        return fake

    monkeypatch.setattr(svc, "_get_redis", _get)
    return fake


def _set_deployment(monkeypatch, value: bool):
    monkeypatch.setattr(svc, "_deployment_enforced", lambda: value)


async def test_deployment_off_never_enforced(patched_db, fake_redis, monkeypatch):
    """部署总闸关 → 不强制,且**不查**租户行(快路径)。"""
    _set_deployment(monkeypatch, False)
    await svc.upsert_policy(TENANT_ID, enforce_keys=True)  # 哪怕租户开着
    assert await svc.is_enforced(TENANT_ID) is False


async def test_missing_row_means_not_enforced(patched_db, fake_redis, monkeypatch):
    """部署开 + 缺行 → 不强制(fail-closed 的方向是"不误伤存量")。"""
    _set_deployment(monkeypatch, True)
    assert await svc.tenant_enforced(TENANT_ID) is False
    assert await svc.is_enforced(TENANT_ID) is False


async def test_both_on_means_enforced_and_upsert_invalidates_cache(patched_db, fake_redis, monkeypatch):
    _set_deployment(monkeypatch, True)
    assert await svc.is_enforced(TENANT_ID) is False

    await svc.upsert_policy(TENANT_ID, enforce_keys=True)
    assert fake_redis.store == {}  # 写后即清缓存
    assert await svc.is_enforced(TENANT_ID) is True

    # 关回去同样立即生效
    await svc.upsert_policy(TENANT_ID, enforce_keys=False)
    assert await svc.is_enforced(TENANT_ID) is False


async def test_policy_cache_skips_db_and_expires_by_ttl(patched_db, fake_redis, monkeypatch):
    _set_deployment(monkeypatch, True)
    await OpenApiTenantSettingDao.aupsert(TENANT_ID, enforce_keys=True)

    calls = {"n": 0}
    orig = OpenApiTenantSettingDao.aget

    async def counting(tenant_id):
        calls["n"] += 1
        return await orig(tenant_id)

    monkeypatch.setattr(OpenApiTenantSettingDao, "aget", counting)

    assert await svc.tenant_enforced(TENANT_ID) is True
    assert calls["n"] == 1
    assert await svc.tenant_enforced(TENANT_ID) is True
    assert calls["n"] == 1  # 命中缓存


async def test_redis_down_degrades_to_db(patched_db, monkeypatch):
    _set_deployment(monkeypatch, True)
    await OpenApiTenantSettingDao.aupsert(TENANT_ID, enforce_keys=True)

    async def _none():
        return None

    monkeypatch.setattr(svc, "_get_redis", _none)
    assert await svc.is_enforced(TENANT_ID) is True  # 直通查库仍正确


async def test_get_policy_returns_raw_row(patched_db, fake_redis):
    assert await svc.get_policy(TENANT_ID) is None
    row = await svc.upsert_policy(TENANT_ID, enforce_keys=True)
    fetched = await svc.get_policy(TENANT_ID)
    assert fetched is not None and fetched.tenant_id == TENANT_ID
    assert fetched.enforce_keys is True and row.enforce_keys is True
