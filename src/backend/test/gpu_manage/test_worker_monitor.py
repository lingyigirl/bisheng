"""Unit tests for platform celery worker heartbeat monitoring."""

import time

from bisheng.core.config.gpu_manage import GpuManageConf
from bisheng.gpu_manage.domain.services import worker_service


class _FakeRedis:
    def __init__(self, data: dict):
        self.data = data

    async def ahgetall(self, key):
        return dict(self.data.get(key, {}))


async def test_list_workers_alive_and_dead(monkeypatch, mock_settings):
    mock_settings.gpu_manage = GpuManageConf()
    monkeypatch.setattr(worker_service, "settings", mock_settings)
    now = int(time.time())
    data = {
        worker_service.WORKER_ALIVE_KEY: {
            "celery": str(now),
            "knowledge_celery": str(now - 100),  # stale -> dead
        }
    }

    async def get_fake_redis():
        return _FakeRedis(data)

    monkeypatch.setattr(worker_service, "get_redis_client", get_fake_redis)

    workers = await worker_service.list_workers()
    by_queue = {w.queue: w for w in workers}
    assert by_queue["celery"].alive is True
    assert by_queue["knowledge_celery"].alive is False
    assert by_queue["celery"].age_seconds == 0


async def test_list_workers_empty(monkeypatch):
    async def get_fake_redis():
        return _FakeRedis({})

    monkeypatch.setattr(worker_service, "get_redis_client", get_fake_redis)

    assert await worker_service.list_workers() == []
