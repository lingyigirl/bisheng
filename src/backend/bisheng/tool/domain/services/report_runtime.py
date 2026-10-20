"""Bounded isolated conversion with content-keyed disk cache and singleflight."""

import asyncio
import base64
import hashlib
import json
import os
import tempfile
import time
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path

from loguru import logger

from bisheng.tool.domain.services.report_process import execute_report


@dataclass
class _Flight:
    task: asyncio.Task
    waiters: int = 0


@asynccontextmanager
async def _no_capacity(*args):
    yield


class ReportRuntime:
    def __init__(self, root=None, *, executor=execute_report, capacity=None, flight_lock=None):
        self.root = Path(root or Path(tempfile.gettempdir()) / "bisheng-report-cache-v1")
        self.executor = executor
        self.capacity = capacity or _no_capacity
        self.flight_lock = flight_lock or _no_capacity
        self.flights = {}

    def _payload(self, scope, source, resources, target):
        from bisheng.common.utils.report_document import CSS, MAX_RESOURCES, MAX_SOURCE

        if (
            len(source.encode("utf-8")) > MAX_SOURCE
            or len(resources) > 50
            or sum(map(len, resources.values())) > MAX_RESOURCES
        ):
            raise ValueError("Report source or resource limit exceeded")
        payload = {
            "source": source,
            "resources": {key: base64.b64encode(value).decode() for key, value in sorted(resources.items())},
            "target": target,
        }
        version = [
            "report-v1",
            scope,
            CSS,
            os.name,
            os.environ.get("BISHENG_REPORT_PDF_ENGINE", "auto"),
            os.environ.get("BISHENG_REPORT_CHROMIUM_PATH"),
            os.environ.get("BISHENG_SOFFICE_PATH"),
            os.environ.get("BISHENG_REPORT_RENDER_REVISION", "1"),
        ]
        from bisheng.common.utils import report_document
        from bisheng.tool.domain.services import report_validation, report_worker

        # Code and engine changes invalidate cached bytes without touching user files.
        version.extend(
            hashlib.sha256(Path(module.__file__).read_bytes()).hexdigest()
            for module in (report_document, report_worker, report_validation)
        )
        executable = report_worker.soffice_path()
        if executable:
            stat = Path(executable).stat()
            version.extend([executable, stat.st_size, stat.st_mtime_ns])
        key = hashlib.sha256(json.dumps([version, payload], ensure_ascii=False, sort_keys=True).encode()).hexdigest()
        return key, payload

    async def convert(self, scope, source, resources, target):
        key, payload = await asyncio.to_thread(self._payload, scope, source, resources, target)
        flight = self.flights.get(key)
        if flight is None:
            flight = _Flight(asyncio.create_task(self._convert(key, payload)))
            self.flights[key] = flight
        flight.waiters += 1
        try:
            return await asyncio.shield(flight.task)
        finally:
            flight.waiters -= 1
            if not flight.waiters:
                self.flights.pop(key, None)
                if not flight.task.done():
                    flight.task.cancel()
                    try:
                        await flight.task
                    except asyncio.CancelledError:
                        # The last subscriber left; the worker's finally block reaps its process tree.
                        pass

    async def _convert(self, key, payload):
        self.root.mkdir(parents=True, exist_ok=True)
        output, metadata = self.root / (key + ".bin"), self.root / (key + ".json")

        def cached():
            try:
                if output.is_file() and metadata.is_file() and time.time() - output.stat().st_mtime < 86400:
                    data = output.read_bytes()
                    info = json.loads(metadata.read_text(encoding="utf-8"))
                    if hashlib.sha256(data).hexdigest() == info["sha256"]:
                        return data, info["manifest"]
            except (OSError, ValueError, KeyError):
                # Cache is optional and may race eviction in another process; regenerate it.
                logger.warning("Ignoring an incomplete report cache entry {}", key)
            return None

        hit = await asyncio.to_thread(cached)
        if hit:
            return hit
        async with self.flight_lock(key), self.capacity():
            hit = await asyncio.to_thread(cached)
            if hit:
                return hit
            with tempfile.TemporaryDirectory(prefix="job-", dir=self.root) as tmp:
                directory = Path(tmp)
                await asyncio.to_thread((directory / "input.json").write_text, json.dumps(payload), encoding="utf-8")
                await self.executor(directory)
                data = await asyncio.to_thread((directory / "output.bin").read_bytes)
                manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
                info = {"sha256": hashlib.sha256(data).hexdigest(), "manifest": manifest}
                unique = uuid.uuid4().hex
                staged = self.root / (key + "." + unique + ".tmp")
                await asyncio.to_thread(staged.write_bytes, data)
                os.replace(staged, output)
                staged.write_text(json.dumps(info), encoding="utf-8")
                os.replace(staged, metadata)
                await asyncio.to_thread(self._prune)
                return data, manifest

    def _prune(self):
        files = []
        for file in self.root.glob("*.bin"):
            try:
                files.append((file, file.stat()))
            except FileNotFoundError:
                # Other processes can evict the same optional cache entry.
                continue
        files.sort(key=lambda item: item[1].st_mtime, reverse=True)
        total = 0
        for file, stat in files:
            total += stat.st_size
            if total > 1024**3 or time.time() - stat.st_mtime > 86400:
                file.unlink(missing_ok=True)
                file.with_suffix(".json").unlink(missing_ok=True)


def default_runtime():
    from bisheng.tool.domain.services.report_capacity import report_capacity, report_flight

    return ReportRuntime(capacity=report_capacity, flight_lock=report_flight)


runtime = default_runtime()
