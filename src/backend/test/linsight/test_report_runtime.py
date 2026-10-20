"""覆盖 AC: AC-18-AC-29 - isolation, bounded work and cache invalidation."""

import asyncio
import json

import pytest

from bisheng.tool.domain.services.report_runtime import ReportRuntime


async def test_singleflight_cache_and_resource_change(tmp_path, monkeypatch):
    calls = []

    async def execute(directory):
        calls.append(directory)
        await asyncio.sleep(0.02)
        (directory / "output.bin").write_bytes(b"valid result")
        (directory / "manifest.json").write_text(json.dumps({"bytes": 12}), encoding="utf-8")

    runtime = ReportRuntime(tmp_path, executor=execute, capacity=None)
    args = ("tenant1/task1", "# report", {}, "docx")
    a, b = await asyncio.gather(runtime.convert(*args), runtime.convert(*args))
    assert a == b and len(calls) == 1
    assert await runtime.convert(*args) == a
    assert len(calls) == 1
    await runtime.convert("tenant1/task1", "# changed", {}, "docx")
    await runtime.convert("tenant2/task1", "# report", {}, "docx")
    assert len(calls) == 3


async def test_failure_is_never_cached_and_tempdirs_are_cleaned(tmp_path):
    async def execute(directory):
        raise RuntimeError("missing renderer")

    runtime = ReportRuntime(tmp_path, executor=execute, capacity=None)
    for _ in range(2):
        with pytest.raises(RuntimeError, match="missing renderer"):
            await runtime.convert("task1", "# report", {}, "pdf")
    assert not list(tmp_path.rglob("input.json"))


async def test_owned_process_timeout_reaps_tree(tmp_path):
    from bisheng.tool.domain.services.report_process import execute_report

    (tmp_path / "input.json").write_text("{}", encoding="utf-8")
    with pytest.raises((TimeoutError, RuntimeError)):
        await execute_report(tmp_path, timeout=0.001)


async def test_cancel_last_subscriber_stops_work_without_caching(tmp_path):
    started, stopped = asyncio.Event(), asyncio.Event()

    async def execute(directory):
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            stopped.set()

    runtime = ReportRuntime(tmp_path, executor=execute)
    request = asyncio.create_task(runtime.convert("task", "# report", {}, "pdf"))
    await started.wait()
    request.cancel()
    with pytest.raises(asyncio.CancelledError):
        await request
    assert stopped.is_set() and not runtime.flights
    assert not list(tmp_path.glob("*.bin"))


async def test_changed_image_bytes_invalidate_result(tmp_path):
    calls = []

    async def execute(directory):
        calls.append(directory)
        (directory / "output.bin").write_bytes(b"valid result")
        (directory / "manifest.json").write_text("{}", encoding="utf-8")

    runtime = ReportRuntime(tmp_path, executor=execute)
    await runtime.convert("task", "# report", {"image.png": b"first"}, "docx")
    await runtime.convert("task", "# report", {"image.png": b"second"}, "docx")
    assert len(calls) == 2


async def test_owned_process_cleanup_stops_descendants(tmp_path):
    import os
    import sys

    from bisheng.tool.domain.services.report_process import ProcessOwner

    pidfile = tmp_path / "child.pid"
    code = (
        "import subprocess,sys,time;from pathlib import Path;"
        'p=subprocess.Popen([sys.executable,"-c","import time;time.sleep(60)"]);'
        "Path(sys.argv[1]).write_text(str(p.pid));time.sleep(60)"
    )
    owner = ProcessOwner()
    process = await asyncio.create_subprocess_exec(
        sys.executable, "-c", code, str(pidfile), start_new_session=os.name != "nt"
    )
    owner.attach(process.pid)
    try:
        for _ in range(100):
            if pidfile.exists():
                break
            await asyncio.sleep(0.02)
        assert pidfile.exists()
        child_pid = int(pidfile.read_text())
    finally:
        await owner.close(process)
    assert process.returncode is not None
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes

        handle = owner.kernel.OpenProcess(0x1000, False, child_pid)
        if handle:
            try:
                status = wintypes.DWORD()
                owner.kernel.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
                owner.kernel.GetExitCodeProcess(handle, ctypes.byref(status))
                assert status.value != 259
            finally:
                owner.kernel.CloseHandle(handle)
    else:
        from pathlib import Path

        stat = Path(f"/proc/{child_pid}/stat")
        assert not stat.exists() or stat.read_text().split()[2] == "Z"


async def test_real_validation_process_timeout_cleans_owned_directory(tmp_path):
    """AC-24, AC-29: timeout kills the parser instead of leaving a background thread."""
    import base64
    import json

    from bisheng.tool.domain.services.report_process import execute_report

    directory = tmp_path / "owned-validation"
    directory.mkdir()
    payload = {
        "target": "validate",
        "resources": {},
        "references": [],
        "files": [("a.csv", base64.b64encode(b"a,b\n1,2\n").decode())],
    }
    (directory / "input.json").write_text(json.dumps(payload), encoding="utf8")
    with pytest.raises(TimeoutError):
        await execute_report(directory, timeout=0.001)
    # On Windows, removal also proves no owned child still holds these files.
    (directory / "input.json").unlink()
    directory.rmdir()
