"""F078 T-16: per-tenant/agent fixed-window limiter."""

import pytest

from bisheng.common.errcode.managed_agent import ManagedAgentRateLimitedError
from bisheng.common.errcode.open_api import OpenApiAuthBackendUnavailableError
from bisheng.common.services.config_service import settings
from bisheng.managed_agent.domain.services import rate_limiter


class _FakeConnection:
    def __init__(self):
        self.data = {}
        self.set_calls = []

    async def set(self, key, value, nx=None, ex=None):
        self.set_calls.append((key, nx, ex))
        if nx and key in self.data:
            return False
        self.data[key] = value
        return True

    async def incr(self, key):
        self.data[key] = int(self.data.get(key, 0)) + 1
        return self.data[key]


class _FakeRedis:
    def __init__(self, conn):
        self.async_connection = conn


@pytest.fixture()
def fake_redis(monkeypatch):
    conn = _FakeConnection()
    redis = _FakeRedis(conn)

    async def _get():
        return redis

    monkeypatch.setattr(rate_limiter, "get_redis_client", _get)
    return conn


def test_limit_and_key_include_tenant_and_agent(monkeypatch, fake_redis):
    monkeypatch.setattr(settings.managed_agent, "rate_limit_per_minute", 1)
    import asyncio

    asyncio.run(rate_limiter.consume(7, 11, now_epoch=120))
    key = next(iter(fake_redis.data))
    assert key == "managed_agent:rate:7:11:2"
    assert fake_redis.set_calls[0][1] is True
    assert fake_redis.set_calls[0][2] == 120

    with pytest.raises(ManagedAgentRateLimitedError) as exc:
        asyncio.run(rate_limiter.consume(7, 11, now_epoch=121))
    assert exc.value.kwargs["retry_after"] == 59


def test_redis_failure_fails_closed(monkeypatch):
    class _Broken:
        async def set(self, *args, **kwargs):
            raise RuntimeError("down")

    class _Redis:
        async_connection = _Broken()

    async def _get():
        return _Redis()

    monkeypatch.setattr(rate_limiter, "get_redis_client", _get)
    import asyncio

    with pytest.raises(OpenApiAuthBackendUnavailableError):
        asyncio.run(rate_limiter.consume(7, 11))
