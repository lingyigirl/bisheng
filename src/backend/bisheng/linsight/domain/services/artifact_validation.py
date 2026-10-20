"""Reuse verified manifests for unchanged deliverables, with bounded memory."""

import asyncio
import base64
import hashlib
import json
import tempfile
import threading
from collections import OrderedDict
from pathlib import Path
from urllib.parse import unquote, urlsplit

from bisheng.common.utils.report_document import MAX_SOURCE, resource_references
from bisheng.tool.domain.services.report_capacity import report_capacity
from bisheng.tool.domain.services.report_process import execute_report

_manifests = OrderedDict()
_cache_lock = threading.Lock()


def _prepare_file(path, workspace, svid=None):
    file = Path(path)
    if file.stat().st_size > 64 * 1024 * 1024:
        raise ValueError("Artifact exceeds 64 MiB")
    data = file.read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    resources, versions, resource_data = [], [], []
    if file.suffix.lower() in {".md", ".markdown", ".html", ".htm"}:
        if len(data) > MAX_SOURCE:
            raise ValueError("Report source exceeds 2 MiB")
        root = Path(workspace).resolve()
        references = resource_references(data.decode("utf-8-sig"))
        if len(references) > 50:
            raise ValueError("Report resource count exceeds 50")
        total = 0
        for ref in references:
            if ref.startswith("data:"):
                try:
                    mime, encoded = ref.split(",", 1)
                    kind = mime.removeprefix("data:image/").removesuffix(";base64")
                    if kind not in {"png", "jpeg", "gif", "webp"} or not mime.endswith(";base64"):
                        raise ValueError("Unsupported embedded image")
                    blob = base64.b64decode(encoded, validate=True)
                    resource_data.append(("embedded." + kind, blob))
                except ValueError as exc:
                    raise ValueError("Invalid embedded report image") from exc
                continue
            if urlsplit(ref).scheme or ref.startswith("//"):
                raise ValueError("Report image must be copied into the task workspace")
            value = unquote(ref).replace("\\", "/")
            if "workspace/" in value:
                marker = f"workspace/{svid}/"
                if not svid or marker not in value:
                    raise ValueError("Report image belongs to another workspace")
                value = "/" + value.split(marker, 1)[1]
            candidate = (
                root / value.lstrip("/")
                if value.startswith(("/output/", "/uploads/", "/scratch/", "output/", "uploads/", "scratch/"))
                else file.parent / value
            ).resolve()
            if not candidate.is_relative_to(root) or not candidate.is_file():
                raise ValueError(f"Missing or invalid report image: {ref}")
            if candidate.stat().st_size > 10 * 1024 * 1024:
                raise ValueError("Report image exceeds 10 MiB")
            blob = candidate.read_bytes()
            total += len(blob)
            if total > 64 * 1024 * 1024:
                raise ValueError("Report resources exceed 64 MiB")
            resources.append(ref)
            versions.append((ref, hashlib.sha256(blob).hexdigest()))
            resource_data.append((candidate.name, blob))
    key = (str(Path(workspace).resolve()), str(file.resolve()), digest, tuple(versions))
    return key, [(file.name, data), *resource_data], resources


async def validate_deliverable(path, workspace, svid=None):
    key, files, references = await asyncio.to_thread(_prepare_file, path, workspace, svid)
    with _cache_lock:
        cached = _manifests.get(key)
        if cached is not None:
            _manifests.move_to_end(key)
            return cached
    async with report_capacity():
        with tempfile.TemporaryDirectory(prefix="bisheng-validate-") as tmp:
            directory = Path(tmp)

            def write_input():
                payload = {
                    "target": "validate",
                    "resources": {},
                    "references": references,
                    "files": [(name, base64.b64encode(data).decode("ascii")) for name, data in files],
                }
                (directory / "input.json").write_text(json.dumps(payload), encoding="utf-8")

            await asyncio.to_thread(write_input)
            # Cancellation/timeout reaps this owned process tree on both platforms.
            await execute_report(directory, timeout=30)
            manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
    with _cache_lock:
        _manifests[key] = manifest
        while len(_manifests) > 256:
            _manifests.popitem(last=False)
    return manifest
