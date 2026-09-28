import logging
from abc import ABC
from typing import Union

from bisheng.core.config.settings import ObjectStore
from bisheng.core.storage.base import BaseStorage
from bisheng.core.storage.local.local_storage import LocalStorage
from bisheng.core.context import BaseContextManager
from bisheng.core.storage.minio.minio_storage import MinioStorage

logger = logging.getLogger(__name__)


class MinioManager(BaseContextManager[BaseStorage]):
    """Object Storage Global Manager

    Picks the backend from ``object_storage.type``: ``minio`` (default) or ``local``.
    The context name stays "minio" so existing lookups keep working.
    """

    name: str = "minio"

    def __init__(
            self,
            object_storage_config: Union[ObjectStore, dict],
            **kwargs
    ):
        super().__init__(self.name, **kwargs)
        if isinstance(object_storage_config, dict):
            object_storage_config = ObjectStore(**object_storage_config)
        self.object_storage_config = object_storage_config

    def _create_storage(self) -> BaseStorage:
        storage_type = (self.object_storage_config.type or "minio").strip().lower()
        if storage_type == "local":
            if not self.object_storage_config.local:
                raise ValueError("object_storage.type=local but object_storage.local is missing")
            return LocalStorage(self.object_storage_config.local)
        if storage_type == "minio":
            if not self.object_storage_config.minio:
                raise ValueError("object_storage.type=minio but object_storage.minio is missing")
            return MinioStorage(self.object_storage_config.minio)
        raise ValueError(f"unsupported object_storage.type: {storage_type}")

    async def _async_initialize(self) -> BaseStorage:
        return self._create_storage()

    def _sync_initialize(self) -> BaseStorage:
        return self._create_storage()

    def _sync_cleanup(self) -> None:
        if self._instance:
            self._instance.close_sync()

    async def _async_cleanup(self) -> None:
        if self._instance:
            await self._instance.close()


async def get_minio_storage() -> BaseStorage:
    """Dapatkan Minio Storage Instance"""
    from bisheng.core.context.manager import app_context
    try:
        return await app_context.async_get_instance(MinioManager.name)
    except KeyError:
        logger.warning(f"MinioManager not found in app_context. Registering a new instance.")
        try:
            from bisheng.common.services.config_service import settings
            app_context.register_context(MinioManager(
                object_storage_config=settings.object_storage
            ))
            return await app_context.async_get_instance(MinioManager.name)
        except Exception as e:
            logger.error(f"Failed to register MinioManager: {e}")
            raise


def get_minio_storage_sync() -> BaseStorage:
    """Synchronous fetch Minio Storage Instance"""
    from bisheng.core.context.manager import app_context
    try:
        return app_context.sync_get_instance(MinioManager.name)
    except KeyError:
        logger.warning(f"MinioManager not found in app_context. Registering a new instance.")
        try:
            from bisheng.common.services.config_service import settings
            app_context.register_context(MinioManager(
                object_storage_config=settings.object_storage
            ))
            return app_context.sync_get_instance(MinioManager.name)
        except Exception as e:
            logger.error(f"Failed to register MinioManager: {e}")
            raise
