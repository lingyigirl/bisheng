"""Execution-scoped fences shared by graph, tools, database and event writers."""

import asyncio
from contextlib import asynccontextmanager
from contextvars import ContextVar
from dataclasses import dataclass

from redis.exceptions import WatchError


class RecoveryLeaseLost(asyncio.CancelledError):
    """An obsolete execution must not run the ordinary failure writer."""


class RecoveryBlocked(RuntimeError):
    """Recovery lacks enough evidence to safely repeat execution."""


@dataclass
class ExecutionFence:
    task_id: str
    tenant_id: int
    token: str
    epoch: int
    redis: object
    sync_redis: object
    lost: bool = False

    @property
    def prefix(self):
        return f"linsight:replay:{{{self.tenant_id}:{self.task_id}}}"

    @property
    def lease_key(self):
        return self.prefix + ":lease"

    async def check(self):
        current = await self.redis.get(self.lease_key)
        if self.lost or current != self.token.encode():
            self.lost = True
            raise RecoveryLeaseLost("Execution lease expired or superseded")

    def check_sync(self):
        if self.lost or self.sync_redis.get(self.lease_key) != self.token.encode():
            self.lost = True
            raise RecoveryLeaseLost("Execution lease expired or superseded")


execution_fence: ContextVar[ExecutionFence | None] = ContextVar("linsight_execution_fence", default=None)


class _FencedPipeline:
    def __init__(self, pipe, fence):
        self.pipe, self.fence = pipe, fence

    def __getattr__(self, name):
        return getattr(self.pipe, name)

    async def execute(self):
        commands = list(self.pipe.command_stack)
        while True:
            try:
                return await self.pipe.execute()
            except WatchError:
                # EXPIRE during healthy renewal also invalidates WATCH. Retry only
                # while the same token owns the lease; aborted MULTI wrote nothing.
                await self.pipe.watch(self.fence.lease_key)
                if await self.pipe.get(self.fence.lease_key) != self.fence.token.encode():
                    raise RecoveryLeaseLost("Lease changed during checkpoint write")
                self.pipe.multi()
                self.pipe.command_stack = list(commands)


@asynccontextmanager
async def fenced_pipeline(client, *, transaction=True):
    """WATCH closes the check/write race; no write survives lease replacement."""
    fence = execution_fence.get()
    async with client.async_pipeline(transaction=True if fence else transaction) as pipe:
        if fence:
            await pipe.watch(fence.lease_key)
            if await pipe.get(fence.lease_key) != fence.token.encode():
                raise RecoveryLeaseLost("Checkpoint writer lost its lease")
            pipe.multi()
        try:
            yield _FencedPipeline(pipe, fence) if fence else pipe
        except WatchError as error:
            raise RecoveryLeaseLost("Lease changed during checkpoint write") from error
