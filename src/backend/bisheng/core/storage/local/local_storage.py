import asyncio
import os
import shutil
from io import BytesIO
from pathlib import Path
from typing import BinaryIO, Union

from bisheng.core.config.settings import LocalConf
from bisheng.core.storage.base import BaseStorage


class _LocalDownloadResponse:
    """Minimal stand-in for the urllib3 response MinIO returns on download."""

    def __init__(self, data: bytes):
        self._buffer = BytesIO(data)

    def read(self, *args, **kwargs) -> bytes:
        return self._buffer.read(*args, **kwargs)

    def close(self) -> None:
        self._buffer.close()

    def release_conn(self) -> None:
        pass


class LocalStorage(BaseStorage):
    """Filesystem-backed storage.

    Layout mirrors MinIO's logical paths: ``{root}/{bucket}/{object_name}``.
    """

    def __init__(self, local_config: LocalConf):
        self.local_config = local_config
        self.root = Path(local_config.root).resolve()
        self.bucket = local_config.public_bucket
        self.tmp_bucket = local_config.tmp_bucket
        self.root.mkdir(parents=True, exist_ok=True)
        self.create_bucket_sync(self.bucket)
        self.create_bucket_sync(self.tmp_bucket)

    def _bucket_dir(self, bucket_name: str) -> Path:
        return self.root / (bucket_name or self.bucket)

    def _object_path(self, bucket_name: str, object_name: str) -> Path:
        # 防止 object_name 携带 ../ 越出存储根目录
        target = (self._bucket_dir(bucket_name) / (object_name or "").lstrip("/")).resolve()
        if target != self.root and self.root not in target.parents:
            raise ValueError(f"illegal object_name escapes storage root: {object_name}")
        return target

    # ------------------------------------------------------------------ bucket

    async def create_bucket(self, bucket_name: str) -> None:
        await asyncio.to_thread(self.create_bucket_sync, bucket_name)

    def create_bucket_sync(self, bucket_name: str) -> None:
        self._bucket_dir(bucket_name).mkdir(parents=True, exist_ok=True)

    async def check_bucket_exists(self, bucket_name: str) -> bool:
        return self.check_bucket_exists_sync(bucket_name)

    def check_bucket_exists_sync(self, bucket_name: str) -> bool:
        return self._bucket_dir(bucket_name).is_dir()

    async def get_all_buckets(self) -> list:
        return self.get_all_buckets_sync()

    def get_all_buckets_sync(self) -> list:
        if not self.root.is_dir():
            return []
        return [item.name for item in self.root.iterdir() if item.is_dir()]

    async def remove_bucket(self, bucket_name: str) -> None:
        await asyncio.to_thread(self.remove_bucket_sync, bucket_name)

    def remove_bucket_sync(self, bucket_name: str) -> None:
        bucket_dir = self._bucket_dir(bucket_name)
        if bucket_dir.is_dir():
            shutil.rmtree(bucket_dir)

    # ------------------------------------------------------------------ object

    async def put_object(
        self,
        *,
        bucket_name: str | None = None,
        object_name: str,
        file: Union[bytes, BinaryIO, Path, str],
        content_type: str = "application/octet-stream",
        **kwargs,
    ) -> None:
        await asyncio.to_thread(
            self.put_object_sync,
            bucket_name=bucket_name,
            object_name=object_name,
            file=file,
            content_type=content_type,
        )

    def put_object_sync(
        self,
        *,
        bucket_name: str | None = None,
        object_name: str,
        file: Union[bytes, BinaryIO, Path, str],
        content_type: str = "application/octet-stream",
        **kwargs,
    ) -> None:
        target = self._object_path(bucket_name, object_name)
        target.parent.mkdir(parents=True, exist_ok=True)
        # 先写临时文件再原子替换，避免并发读到写了一半的文件
        staging = target.with_name(f".{target.name}.{os.getpid()}.part")
        if isinstance(file, bytes):
            staging.write_bytes(file)
        elif isinstance(file, (str, Path)):
            shutil.copyfile(str(file), staging)
        else:
            with open(staging, "wb") as out:
                shutil.copyfileobj(file, out)
        os.replace(staging, target)

    async def put_object_tmp(
        self,
        object_name: str,
        file: Union[bytes, BinaryIO, Path, str],
        content_type: str = "application/octet-stream",
        **kwargs,
    ) -> None:
        await self.put_object(
            bucket_name=self.tmp_bucket, object_name=object_name, file=file, content_type=content_type, **kwargs
        )

    def put_object_tmp_sync(
        self,
        object_name: str,
        file: Union[bytes, BinaryIO, Path, str],
        content_type: str = "application/octet-stream",
        **kwargs,
    ) -> None:
        self.put_object_sync(
            bucket_name=self.tmp_bucket, object_name=object_name, file=file, content_type=content_type, **kwargs
        )

    async def get_object(self, bucket_name: str | None = None, object_name: str = None) -> bytes | None:
        return await asyncio.to_thread(self.get_object_sync, bucket_name, object_name)

    def get_object_sync(self, bucket_name: str | None = None, object_name: str = None) -> bytes | None:
        if object_name is None:
            raise ValueError("get_object_sync: object_name must be provided")
        target = self._object_path(bucket_name, object_name)
        if not target.is_file():
            return None
        return target.read_bytes()

    def download_object_sync(self, bucket_name: str | None = None, object_name: str = None):
        data = self.get_object_sync(bucket_name, object_name)
        if data is None:
            raise FileNotFoundError(f"object not found: {object_name}")
        return _LocalDownloadResponse(data)

    async def object_exists(self, bucket_name: str | None = None, object_name: str = None) -> bool:
        return self.object_exists_sync(bucket_name, object_name)

    def object_exists_sync(self, bucket_name: str | None = None, object_name: str = None) -> bool:
        if not object_name:
            return False
        try:
            return self._object_path(bucket_name, object_name).is_file()
        except ValueError:
            return False

    def get_object_size_sync(self, bucket_name: str | None = None, object_name: str = None) -> int:
        if object_name is None:
            raise ValueError("get_object_size_sync: object_name must be provided")
        target = self._object_path(bucket_name, object_name)
        if not target.is_file():
            raise FileNotFoundError(f"object not found: {object_name}")
        return target.stat().st_size

    def list_objects_sync(self, bucket_name: str | None = None, prefix: str = "") -> list:
        bucket_dir = self._bucket_dir(bucket_name)
        if not bucket_dir.is_dir():
            return []
        base = bucket_dir.resolve()
        results = []
        for dirpath, _dirnames, filenames in os.walk(base):
            for filename in filenames:
                full = Path(dirpath) / filename
                rel = full.relative_to(base).as_posix()
                if prefix and not rel.startswith(prefix):
                    continue
                stat = full.stat()
                results.append(
                    _LocalObject(
                        object_name=rel,
                        size=stat.st_size,
                        last_modified=stat.st_mtime,
                    )
                )
        return results

    async def copy_object(
        self, source_bucket: str = None, source_object: str = None, dest_bucket: str = None, dest_object: str = None
    ) -> None:
        await asyncio.to_thread(
            self.copy_object_sync,
            source_bucket=source_bucket,
            source_object=source_object,
            dest_bucket=dest_bucket,
            dest_object=dest_object,
        )

    def copy_object_sync(
        self, source_bucket: str = None, source_object: str = None, dest_bucket: str = None, dest_object: str = None
    ) -> None:
        if source_bucket is None:
            source_bucket = self.tmp_bucket
        if dest_bucket is None:
            dest_bucket = self.bucket
        source = self._object_path(source_bucket, source_object)
        target = self._object_path(dest_bucket, dest_object)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)

    async def remove_object(self, bucket_name: str | None = None, object_name: str = None) -> None:
        await asyncio.to_thread(self.remove_object_sync, bucket_name, object_name)

    def remove_object_sync(self, bucket_name: str | None = None, object_name: str = None) -> None:
        if object_name is None:
            raise ValueError("remove_object_sync: object_name must be provided")
        target = self._object_path(bucket_name, object_name)
        if target.is_file():
            target.unlink()

    # --------------------------------------------------------------- share url

    async def get_share_link(self, object_name, bucket=None, clear_host: bool = True, expire_days: int = 7) -> str:
        return self.get_share_link_sync(object_name, bucket=bucket, clear_host=clear_host, expire_days=expire_days)

    def get_share_link_sync(self, object_name, bucket=None, clear_host: bool = True, expire_days: int = 7) -> str:
        if bucket is None:
            bucket = self.bucket
        return f"/{bucket}/{(object_name or '').lstrip('/')}"

    def get_minio_share_host(self) -> str:
        return self.local_config.share_host or ""

    def clear_minio_share_host(self, file_url: str):
        share_host = self.get_minio_share_host()
        if share_host and file_url.startswith(share_host):
            return file_url[len(share_host):]
        return file_url

    # ---------------------------------------------------------------- lifecycle

    async def close(self) -> None:
        return None

    def close_sync(self) -> None:
        return None


class _LocalObject:
    """Shape-compatible stand-in for a MinIO object listing entry."""

    def __init__(self, object_name: str, size: int, last_modified: float):
        self.object_name = object_name
        self.size = size
        self.last_modified = last_modified