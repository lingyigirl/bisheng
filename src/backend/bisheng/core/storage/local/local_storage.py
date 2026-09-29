import asyncio
import os
import shutil
import time
from io import BytesIO
from pathlib import Path
from typing import BinaryIO, Union
from uuid import uuid4

from bisheng.core.config.settings import LocalConf
from bisheng.core.storage.base import BaseStorage
from bisheng.core.storage.tenant_storage import should_fallback_to_root, translate_to_root_prefix


class _LocalDownloadResponse:
    """Minimal stand-in for the urllib3 response MinIO returns on download."""

    def __init__(self, data: bytes):
        self._buffer = BytesIO(data)

    def read(self, *args, **kwargs) -> bytes:
        return self._buffer.read(*args, **kwargs)

    def stream(self, amt: int = 2 ** 16, decode_content=None):
        # 与 urllib3.HTTPResponse.stream 对齐, 调用方按 stream(chunk_size) 逐块读取
        while True:
            chunk = self._buffer.read(amt)
            if not chunk:
                break
            yield chunk

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
        bucket_dir = (self.root / (bucket_name or self.bucket)).resolve()
        if bucket_dir != self.root and self.root not in bucket_dir.parents:
            raise ValueError(f"illegal bucket escapes storage root: {bucket_name}")
        # 防止 object_name 携带 ../ 越出 bucket, 同时阻止跨 bucket 读写
        target = (bucket_dir / (object_name or "").lstrip("/")).resolve()
        if target != bucket_dir and bucket_dir not in target.parents:
            raise ValueError(f"illegal object_name escapes bucket: {object_name}")
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
        # 先写临时文件再原子替换, 避免并发读到写了一半的文件
        # 临时名带随机后缀, 同进程多线程并发写同一个 object_name 时不会互相覆盖
        staging = target.with_name(f".{target.name}.{uuid4().hex}.part")
        try:
            if isinstance(file, bytes):
                staging.write_bytes(file)
            elif isinstance(file, (str, Path)):
                shutil.copyfile(str(file), staging)
            else:
                with open(staging, "wb") as out:
                    shutil.copyfileobj(file, out)
            os.replace(staging, target)
        except BaseException:
            # 失败时清掉半成品, 避免留下 .part 垃圾文件
            staging.unlink(missing_ok=True)
            raise

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

    async def get_object(self, bucket_name: str | None = None, object_name: str | None = None) -> bytes | None:
        return await asyncio.to_thread(self.get_object_sync, bucket_name, object_name)

    def get_object_sync(self, bucket_name: str | None = None, object_name: str | None = None) -> bytes | None:
        if object_name is None:
            raise ValueError("get_object_sync: object_name must be provided")
        target = self._object_path(bucket_name, object_name)
        if target.is_file():
            return target.read_bytes()
        # F017: 子租户读不到时, 剥掉租户前缀回退到 root 共享路径
        if should_fallback_to_root():
            root_name = translate_to_root_prefix(object_name)
            if root_name is not None:
                root_target = self._object_path(bucket_name, root_name)
                if root_target.is_file():
                    return root_target.read_bytes()
                from bisheng.common.errcode.tenant_sharing import StorageSharingFallbackError

                raise StorageSharingFallbackError()
        raise FileNotFoundError(f"object not found: {bucket_name}/{object_name}")

    def download_object_sync(self, bucket_name: str | None = None, object_name: str | None = None):
        return _LocalDownloadResponse(self.get_object_sync(bucket_name, object_name))

    async def object_exists(self, bucket_name: str | None = None, object_name: str | None = None) -> bool:
        return self.object_exists_sync(bucket_name, object_name)

    def object_exists_sync(self, bucket_name: str | None = None, object_name: str | None = None) -> bool:
        if not object_name:
            return False
        try:
            if self._object_path(bucket_name, object_name).is_file():
                return True
            # F017: 子租户读不到时, 看 root 共享路径是否存在
            if should_fallback_to_root():
                root_name = translate_to_root_prefix(object_name)
                if root_name is not None:
                    return self._object_path(bucket_name, root_name).is_file()
            return False
        except ValueError:
            return False

    def get_object_size_sync(self, bucket_name: str | None = None, object_name: str | None = None) -> int:
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
        self, source_bucket: str | None = None, source_object: str | None = None, dest_bucket: str | None = None, dest_object: str | None = None
    ) -> None:
        await asyncio.to_thread(
            self.copy_object_sync,
            source_bucket=source_bucket,
            source_object=source_object,
            dest_bucket=dest_bucket,
            dest_object=dest_object,
        )

    def copy_object_sync(
        self, source_bucket: str | None = None, source_object: str | None = None, dest_bucket: str | None = None, dest_object: str | None = None
    ) -> None:
        if source_bucket is None:
            source_bucket = self.tmp_bucket
        if dest_bucket is None:
            dest_bucket = self.bucket
        source = self._object_path(source_bucket, source_object)
        target = self._object_path(dest_bucket, dest_object)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)

    async def remove_object(self, bucket_name: str | None = None, object_name: str | None = None) -> None:
        await asyncio.to_thread(self.remove_object_sync, bucket_name, object_name)

    def remove_object_sync(self, bucket_name: str | None = None, object_name: str | None = None) -> None:
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

    def resolve_share_link(self, url: str) -> tuple[str, str] | None:
        """Resolve a local share link (/<bucket>/<object>) into (bucket, object).

        get_share_link() returns the raw path without URL-encoding, so the
        object name is returned verbatim. Returns None for anything that is
        not a path under one of this backend's own buckets.
        """
        if not url or not url.startswith("/"):
            return None
        path_parts = url.split("?", 1)[0].lstrip("/").split("/", 1)
        if len(path_parts) != 2 or not path_parts[1]:
            return None
        if path_parts[0] not in (self.bucket, self.tmp_bucket):
            return None
        return path_parts[0], path_parts[1]

    # ----------------------------------------------------------------- cleanup

    def cleanup_expired_tmp(self) -> int:
        """Purge tmp files older than ``local_config.tmp_expire_days``; returns count."""
        bucket_dir = self._bucket_dir(self.tmp_bucket)
        if not bucket_dir.is_dir():
            return 0
        cutoff = time.time() - self.local_config.tmp_expire_days * 86400
        removed = 0
        # topdown=False so a directory is only tried after its files were removed
        for dirpath, dirnames, filenames in os.walk(bucket_dir, topdown=False):
            for filename in filenames:
                full = Path(dirpath) / filename
                try:
                    if full.stat().st_mtime < cutoff:
                        full.unlink()
                        removed += 1
                except FileNotFoundError:
                    continue
            for dirname in dirnames:
                try:
                    os.rmdir(Path(dirpath) / dirname)
                except OSError:
                    pass
        return removed

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
