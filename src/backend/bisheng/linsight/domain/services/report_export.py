"""Resolve report resources exclusively inside an authorized task workspace."""

import asyncio
import base64
import posixpath
import re
import tempfile
from pathlib import Path
from urllib.parse import unquote, urlsplit

from bisheng.common.utils.report_document import MAX_RESOURCES, MAX_SOURCE, resource_references
from bisheng.linsight.domain.services.workspace_backend import normalize_workspace_path
from bisheng.tool.domain.services.report_runtime import runtime


def workspace_path(path, svid, *, source=None):
    value = unquote(path).replace("\\", "/")
    if urlsplit(value).scheme or value.startswith("//"):
        raise ValueError("External report resources are not supported; copy them into the workspace first")
    marker = f"workspace/{svid}/"
    if marker in value:
        value = value.split(marker, 1)[1]
    elif "workspace/" in value:
        raise ValueError("Report resource belongs to a different task")
    elif source and not value.startswith(("/", "output/", "uploads/", "scratch/")):
        value = posixpath.join(posixpath.dirname(source), value)
    result = normalize_workspace_path(posixpath.normpath(value))
    if result.split("/")[0] not in {"output", "uploads", "scratch"}:
        raise ValueError("Report resources must be in output/, uploads/ or scratch/")
    return result


async def read_workspace_blob(backend, path, max_bytes=MAX_SOURCE):
    # Production reads the object store afresh so edited images cannot hit a stale local cache.
    from bisheng.linsight.domain.services.workspace_backend import WorkspaceBackend

    if isinstance(backend, WorkspaceBackend):
        size = await asyncio.to_thread(backend.minio.get_object_size_sync, backend._bucket(), backend._object_key(path))
        if size > max_bytes:
            raise ValueError(f"Report file exceeds resource limit: {path}")
        return await backend.minio.get_object(bucket_name=backend._bucket(), object_name=backend._object_key(path))
    responses = await backend.adownload_files([path])
    if not responses or responses[0].error or responses[0].content is None:
        raise ValueError(f"Missing workspace file: {path}")
    return bytes(responses[0].content)


async def export_workspace_report(backend, source_path, target, *, source_bytes=None):
    svid = getattr(backend, "svid", "")
    source_path = workspace_path(source_path, svid)
    if not source_path.startswith("output/") or not source_path.lower().endswith((".md", ".markdown", ".html", ".htm")):
        raise ValueError("Report source must be markdown or HTML under output/")
    data = source_bytes if source_bytes is not None else await read_workspace_blob(backend, source_path)
    if len(data) > MAX_SOURCE:
        raise ValueError("Report source exceeds 2 MiB")
    source = data.decode("utf-8-sig")
    refs = await asyncio.to_thread(resource_references, source)
    if len(refs) > 50:
        raise ValueError("Report has more than 50 unique images")
    resources = {}
    for ref in refs:
        if ref.startswith("data:"):
            if not re.fullmatch(r"data:image/(?:png|jpeg|gif|webp);base64,[A-Za-z0-9+/=\s]+", ref):
                raise ValueError("Unsupported image data URI")
            blob = base64.b64decode(ref.split(",", 1)[1], validate=True)
        else:
            rel = workspace_path(ref, svid, source=source_path)
            blob = await read_workspace_blob(backend, rel, max_bytes=10 * 1024 * 1024)
        if len(blob) > 10 * 1024 * 1024:
            raise ValueError(f"Report image exceeds 10 MiB: {ref}")
        resources[ref] = blob
        if sum(map(len, resources.values())) > MAX_RESOURCES:
            raise ValueError("Report resources exceed 64 MiB")
    return await runtime.convert(svid, source, resources, target)


class ReportExportService:
    async def save_as(self, file_info, target, user, share_link=None):
        from bisheng.common.errcode.http_error import UnAuthorizedError
        from bisheng.core.context.tenant import bypass_tenant_filter_if
        from bisheng.core.storage.minio.minio_manager import get_minio_storage
        from bisheng.linsight.domain.repositories.agent_config_repository import AgentConfigRepository
        from bisheng.linsight.domain.services.workspace_backend import WorkspaceBackend
        from bisheng.permission.domain.services.permission_service import PermissionService

        key = unquote(file_info.file_url).lstrip("/")
        match = re.search(r"(?:^|/)(?:workspace|linsight/final_result)/([^/]+)/", key)
        svid = file_info.session_version_id or (match.group(1) if match else None)
        if not svid:
            raise UnAuthorizedError()
        with bypass_tenant_filter_if(share_link is not None):
            version = await AgentConfigRepository().task(svid)
        if version is None or not PermissionService.can_read_task_artifact(user, version, share_link):
            raise UnAuthorizedError()
        minio = await get_minio_storage()
        if key.startswith(minio.bucket + "/"):
            key = key[len(minio.bucket) + 1 :]
        prefix = f"workspace/{svid}/"
        if key.startswith(prefix):
            source = workspace_path(key, svid)
            data = None
        else:
            files = (version.output_result or {}).get("final_files", [])
            found = next((item for item in files if item.get("file_url", "").lstrip("/") == key), None)
            if not found:
                raise UnAuthorizedError()
            source = found.get("workspace_path")
            if not source:
                # Historical artifact metadata includes an executor path, never read it on this host.
                parts = (found.get("file_path") or "").replace("\\", "/").split("/output/", 1)
                source = "output/" + parts[1] if len(parts) == 2 else "output/" + found["file_name"]
            data = await minio.get_object(bucket_name=minio.bucket, object_name=key)
        with tempfile.TemporaryDirectory(prefix="report-source-") as directory:
            backend = WorkspaceBackend(svid, minio, directory)
            result, manifest = await export_workspace_report(backend, source, target, source_bytes=data)
        return Path(file_info.file_name).stem + "." + target, result, manifest
