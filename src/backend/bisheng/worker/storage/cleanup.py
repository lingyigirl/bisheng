"""Celery beat task: purge expired objects from the temporary storage bucket.

MinIO expires tmp objects via bucket lifecycle, so its implementation is a
no-op (inherited from ``BaseStorage``). ``LocalStorage`` walks its tmp
directory and deletes files older than ``object_storage.local.tmp_expire_days``.
"""

from loguru import logger

from bisheng.worker.main import bisheng_celery


@bisheng_celery.task(acks_late=True)
def cleanup_tmp_storage() -> int:
    """Purge expired tmp objects; returns the number removed."""
    from bisheng.core.storage.minio.minio_manager import get_minio_storage_sync

    storage = get_minio_storage_sync()
    removed = storage.cleanup_expired_tmp()
    if removed:
        logger.info("tmp storage cleanup removed {} expired objects", removed)
    return removed