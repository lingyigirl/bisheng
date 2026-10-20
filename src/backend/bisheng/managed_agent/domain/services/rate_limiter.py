"""F078 per-tenant/agent fixed-window limiter.

The F077 limiter does ``INCR`` then ``EXPIRE`` on the first hit; a crash between
the two leaves an immortal key. Here the key is created with ``SET NX EX`` first
(a single round trip that always carries the TTL), then incremented. Redis being
unavailable fails closed with 11812/503 rather than allowing unlimited calls.
"""

from __future__ import annotations

import time

from loguru import logger

from bisheng.common.errcode.managed_agent import ManagedAgentRateLimitedError
from bisheng.common.errcode.open_api import OpenApiAuthBackendUnavailableError
from bisheng.common.services.config_service import settings
from bisheng.core.cache.redis_manager import get_redis_client


def _conf():
    return settings.managed_agent


async def consume(tenant_id: int, agent_id: int, *, now_epoch: int | None = None) -> None:
    now_epoch = int(time.time()) if now_epoch is None else now_epoch
    bucket = now_epoch // 60
    key = f"managed_agent:rate:{tenant_id}:{agent_id}:{bucket}"
    limit = _conf().rate_limit_per_minute
    try:
        redis = await get_redis_client()
        await redis.async_connection.set(key, 0, nx=True, ex=120)
        count = await redis.async_connection.incr(key)
    except Exception as exc:
        logger.exception("managed_agent: rate limiter unavailable")
        raise OpenApiAuthBackendUnavailableError() from exc
    if count > limit:
        retry_after = max(60 - (now_epoch % 60), 1)
        raise ManagedAgentRateLimitedError(
            exception=f"per-minute limit {limit} exceeded",
            retry_after=retry_after,
        )
