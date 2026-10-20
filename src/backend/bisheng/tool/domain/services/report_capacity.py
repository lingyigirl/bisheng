"""Redis leases bound report work across API and worker processes on one host."""

import asyncio
import os
import socket
import time
import uuid
from contextlib import asynccontextmanager

from loguru import logger
from redis.exceptions import RedisError

from bisheng.core.cache.redis_manager import get_redis_client

_ACQUIRE = """
local t=redis.call('TIME'); local now=t[1]*1000+math.floor(t[2]/1000)
redis.call('ZREMRANGEBYSCORE',KEYS[1],'-inf',now)
if redis.call('ZSCORE',KEYS[1],ARGV[1]) then return 1 end
if redis.call('ZCARD',KEYS[1])>=tonumber(ARGV[2]) then return 0 end
redis.call('ZADD',KEYS[1],now+tonumber(ARGV[3]),ARGV[1]); redis.call('PEXPIRE',KEYS[1],ARGV[3]); return 1
"""


async def _redis_call(operation):
    return await asyncio.wait_for(operation, timeout=5)


async def _release(operation):
    try:
        await asyncio.shield(_redis_call(operation))
    except (RedisError, TimeoutError):
        # Leases expire even when middleware is unavailable during cleanup.
        logger.warning("Report lease release unavailable; bounded lease expiry will reclaim it")


@asynccontextmanager
async def report_flight(key):
    client = (await _redis_call(get_redis_client())).async_connection
    host = os.environ.get("BISHENG_REPORT_NODE_ID") or socket.gethostname()
    name = f"bisheng:report:flight:{{{host}}}:{key}"
    token = uuid.uuid4().hex
    deadline = time.monotonic() + 135
    try:
        while not await _redis_call(client.set(name, token, nx=True, ex=150)):
            if time.monotonic() >= deadline:
                raise TimeoutError("Timed out waiting for identical report conversion")
            await asyncio.sleep(0.1)
        yield
    finally:
        await _release(
            client.eval(
                "if redis.call('GET',KEYS[1])==ARGV[1] then return redis.call('DEL',KEYS[1]) end return 0",
                1,
                name,
                token,
            )
        )


@asynccontextmanager
async def report_capacity():
    client = (await _redis_call(get_redis_client())).async_connection
    host = os.environ.get("BISHENG_REPORT_NODE_ID") or socket.gethostname()
    prefix = f"bisheng:report:capacity:{{{host}}}"
    token = uuid.uuid4().hex
    # Both queued and active jobs count toward the six-request admission ceiling.
    try:
        admitted = await _redis_call(client.eval(_ACQUIRE, 1, prefix + ":admitted", token, 6, 180000))
        if not admitted:
            raise RuntimeError("Report conversion queue is full; retry later")
        deadline = time.monotonic() + 15
        while not await _redis_call(client.eval(_ACQUIRE, 1, prefix + ":running", token, 2, 165000)):
            if time.monotonic() >= deadline:
                raise TimeoutError("Report conversion queue wait exceeded 15 seconds")
            await asyncio.sleep(0.1)
        yield
    finally:
        await _release(client.zrem(prefix + ":running", token))
        await _release(client.zrem(prefix + ":admitted", token))
