"""Bounded discovery and batched leases preserve recovery ownership rules."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import fakeredis.aioredis

from bisheng.linsight.domain.services import recovery_service


async def test_scan_pages_candidates_batches_leases_and_deduplicates_enqueue(monkeypatch):
    async with fakeredis.aioredis.FakeRedis() as redis:
        tasks = [SimpleNamespace(id=str(i), tenant_id=i + 1, recovery_state={}) for i in range(4)]
        tasks[2].recovery_state = {"retry_after": float("inf")}
        await redis.set("linsight:replay:{1:0}:lease", "live-owner")
        repository = SimpleNamespace(
            candidates=AsyncMock(side_effect=[tasks[:2], tasks[2:], [], tasks[:2], tasks[2:], []])
        )
        monkeypatch.setattr(recovery_service, "RecoveryRepository", lambda: repository)
        monkeypatch.setattr(
            recovery_service, "get_redis_client", AsyncMock(return_value=SimpleNamespace(async_connection=redis))
        )
        pipeline = redis.pipeline
        batches = []

        def track(*args, **kwargs):
            batch = pipeline(*args, **kwargs)
            batches.append(batch)
            return batch

        monkeypatch.setattr(redis, "pipeline", track)
        service = recovery_service.RecoveryService()
        service.SCAN_BATCH = 2
        queue = SimpleNamespace(put_head=AsyncMock())
        await service.scan(queue)
        await service.scan(queue)
        assert [call.args[0]["session_version_id"] for call in queue.put_head.await_args_list] == ["1", "3"]
        assert len(batches) == 4
        assert repository.candidates.await_args_list[1].kwargs == {"after_id": "1", "limit": 2}
        assert repository.candidates.await_args_list[2].kwargs == {"after_id": "3", "limit": 2}
        assert await redis.get("linsight:replay:{1:0}:lease") == b"live-owner"
