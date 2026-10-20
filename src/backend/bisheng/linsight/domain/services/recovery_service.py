"""Bounded automatic takeover of fenced, recoverable task executions."""

import asyncio
import contextlib
import time
from uuid import uuid4

from loguru import logger

from bisheng.core.cache.redis_manager import get_redis_client
from bisheng.core.context.tenant import current_tenant_id, set_current_tenant_id
from bisheng.linsight.domain.repositories.recovery_repository import RecoveryRepository
from bisheng.linsight.domain.services.recovery_context import (
    ExecutionFence,
    RecoveryBlocked,
    RecoveryLeaseLost,
    execution_fence,
)
from bisheng.linsight.domain.services.recovery_middleware import validate_receipts
from bisheng.linsight.domain.services.recovery_queue_scope import recovery_queue_namespace, recovery_scan_due

_RENEW = """
if redis.call('GET', KEYS[1]) ~= ARGV[1] then return 0 end
redis.call('EXPIRE', KEYS[1], ARGV[2]); return 1
"""
_RELEASE = """
if redis.call('GET', KEYS[1]) ~= ARGV[1] then return 0 end
return redis.call('DEL', KEYS[1])
"""


class RecoveryService:
    LEASE_TTL = 30
    SCAN_INTERVAL = 5
    MAX_ATTEMPTS = 3
    SCAN_BATCH = 256

    async def prepare_continue(self, task_id, question):
        return await RecoveryRepository().prepare_continue(task_id, question)

    async def terminate(self, task_id):
        task = await RecoveryRepository().terminate(task_id)
        client = await get_redis_client()
        await client.async_connection.delete(f"linsight:replay:{{{task.tenant_id}:{task.id}}}:lease")
        return task

    @staticmethod
    def enabled(task):
        return isinstance(getattr(task, "agent_config", None), dict) and task.agent_config.get("recovery_protocol") == 1

    async def run(self, task, item, executor, node_manager):
        client = await get_redis_client()
        redis = client.async_connection
        prefix = f"linsight:replay:{{{task.tenant_id}:{task.id}}}"
        key, token = prefix + ":lease", uuid4().hex
        if not await redis.set(key, token, nx=True, ex=self.LEASE_TTL):
            return
        recovering = bool(item.get("recover"))
        tenant_token = set_current_tenant_id(task.tenant_id)
        context_token = None
        renewal = None
        parent = asyncio.current_task()
        fence = None
        task_id = task.id

        async def renew():
            while True:
                await asyncio.sleep(self.LEASE_TTL / 3)
                try:
                    ok = await redis.eval(_RENEW, 1, key, token, self.LEASE_TTL)
                except Exception:
                    logger.exception("Recovery lease renewal failed for {}", task_id)
                    ok = False
                if not ok:
                    if fence:
                        fence.lost = True
                    parent.cancel()
                    return

        try:
            # Database row-lock waits must not leave an unrenewed Redis owner.
            renewal = asyncio.create_task(renew())
            task = await RecoveryRepository().claim(task_id, recovering=recovering)
            if task is None:
                return
            fence = ExecutionFence(task.id, task.tenant_id, token, task.recovery_epoch, redis, client.connection)
            context_token = execution_fence.set(fence)
            await node_manager.register_task_ownership(task.id)
            continuation = (task.recovery_state or {}).get("continuation")
            minimum_epoch = continuation["receipt_epoch"] if continuation else 0
            if recovering:
                if not await redis.exists(prefix + ":recovery-marker"):
                    raise RecoveryBlocked("Recovery metadata expired; safe replay cannot be established")
                if task.recovery_state["attempts"] > self.MAX_ATTEMPTS:
                    raise RecoveryBlocked("Automatic recovery retry limit reached")
                await validate_receipts(fence, minimum_epoch=minimum_epoch)
                if continuation:
                    executor._crash_recovery = True
                    await executor.async_continue(task.id, question=continuation["question"])
                else:
                    await executor.async_recover(task.id)
            else:
                await redis.set(prefix + ":recovery-marker", "1", ex=604800)
                if task.recovery_epoch == 1:
                    await redis.set(prefix + ":receipt-count", "0", nx=True, ex=604800)
                else:
                    await validate_receipts(fence, minimum_epoch=minimum_epoch)
                if item.get("resume"):
                    await executor.async_resume(task.id, user_input=item.get("user_input"))
                elif continuation:
                    await executor.async_continue(task.id, question=continuation["question"])
                elif item.get("continue_question") is not None:
                    await executor.async_continue(task.id, question=item["continue_question"])
                else:
                    await executor.async_run(task.id)
        except RecoveryBlocked as error:
            executor.session_version_id = task.id
            from bisheng.linsight.domain.services.state_message_manager import LinsightStateMessageManager

            executor._state_manager = LinsightStateMessageManager(task.id)
            await executor._handle_execution_error(error)
        except (RecoveryLeaseLost, asyncio.CancelledError):
            logger.warning("Execution stopped after lease loss/cancellation: {}", task_id)
        finally:
            if renewal:
                renewal.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await renewal
            if context_token is not None:
                execution_fence.reset(context_token)
            current_tenant_id.reset(tenant_token)
            await redis.eval(_RELEASE, 1, key, token)

    async def scan(self, queue):
        client = await get_redis_client()
        redis = client.async_connection
        after_id = None
        repository = RecoveryRepository()
        while True:
            tasks = await repository.candidates(after_id=after_id, limit=self.SCAN_BATCH)
            if not tasks:
                return
            eligible = [
                task
                for task in tasks
                if recovery_scan_due(task.recovery_state, time.time(), recovery_queue_namespace())
            ]
            async with redis.pipeline(transaction=False) as pipeline:
                for task in eligible:
                    pipeline.exists(f"linsight:replay:{{{task.tenant_id}:{task.id}}}:lease")
                leases = await pipeline.execute()
            for task, leased in zip(eligible, leases, strict=True):
                if not leased and await redis.set(
                    f"linsight:replay:{{{task.tenant_id}:{task.id}}}:scheduled", "1", nx=True, ex=30
                ):
                    await queue.put_head({"session_version_id": task.id, "recover": True})
            if len(tasks) < self.SCAN_BATCH:
                return
            after_id = tasks[-1].id

    async def monitor(self, queue):
        while True:
            try:
                await self.scan(queue)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Automatic recovery scan failed")
            await asyncio.sleep(self.SCAN_INTERVAL)
