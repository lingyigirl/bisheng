"""Platform Celery worker heartbeat monitoring (Redis hash)."""

import time

from bisheng.common.services.config_service import settings
from bisheng.core.cache.redis_manager import get_redis_client
from bisheng.gpu_manage.domain.schemas.monitor import WorkerStatus

# Same key written by worker_alive_beat in bisheng/worker/main.py. Referenced as
# a constant here on purpose: importing bisheng.worker.main from the API process
# would pull in the whole celery app.
WORKER_ALIVE_KEY = "celery_worker_alive_queues"


async def list_workers() -> list[WorkerStatus]:
    redis_client = await get_redis_client()
    raw = await redis_client.ahgetall(WORKER_ALIVE_KEY)
    now = int(time.time())
    threshold = settings.gpu_manage.worker_alive_threshold
    workers = []
    for queue, beat_ts in raw.items():
        last_beat = int(beat_ts)
        age = max(0, now - last_beat)
        workers.append(WorkerStatus(queue=queue, last_beat_ts=last_beat, age_seconds=age, alive=age <= threshold))
    workers.sort(key=lambda item: item.queue)
    return workers
