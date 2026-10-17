"""TC-4: E2B sandbox copy-in / copy-out for the F035 workspace integration.

The sandbox cannot reach MinIO; the worker mediates all file transfer
(design §9.3.9). These tests use a ``FakeSandbox`` mimicking the e2b
``sandbox.files`` API and a tiny in-memory workspace backend; no real E2B or
MinIO is involved.

Coverage:
  - copy-in: ``run(code, required_files=[])`` new param; small files
    auto-pushed (delta by md5, hit -> skipped); large files must be declared
    in ``required_files``; undeclared large files surface a hint.
  - copy-out: full-tree scan finds new files vs a pre-run snapshot; result
    appends ``new_files: [{path, size, is_product}]`` (``output/`` -> product);
    products are written back through the workspace backend.
    A file MODIFIED after its first write is re-copied too (mtime-based).
  - workspace→sandbox delta sync: local changes (mtime_ns+size), deletions and
    restores of run-deleted files; toolchain/oversize entries skipped; state
    rolled back on failure so the next run retries.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass

import pytest

from bisheng_langchain.gpts.tools.code_interpreter.e2b_executor import (
    SIZE_AUTOPUSH,
    SIZE_INLINE,
    E2bCodeExecutor,
)


# ---------------------------------------------------------------------------
# Fake e2b sandbox
# ---------------------------------------------------------------------------
@dataclass
class FakeEntry:
    name: str
    path: str
    type: str  # "file" | "dir"
    modified_time: float = 0.0


class FakeExecution:
    def __init__(self):
        self.results = []

        class _Logs:
            stdout = ""
            stderr = ""

        self.logs = _Logs()
        self.error = None


class FakeFiles:
    def __init__(self):
        # path -> bytes
        self.store: dict[str, bytes] = {}
        self.mtime: dict[str, float] = {}

    def make_dir(self, path):
        pass

    def write(self, *args):
        # supports write(list[{path,data}]) or write(path, data)
        if len(args) == 1 and isinstance(args[0], list):
            for item in args[0]:
                self.store[item["path"]] = item["data"]
                self.mtime[item["path"]] = time.time()
        else:
            path, data = args
            self.store[path] = data if isinstance(data, bytes) else bytes(data)
            self.mtime[path] = time.time()

    def list(self, path):
        entries = []
        for p in sorted(self.store):
            entries.append(FakeEntry(name=p.split("/")[-1], path=p, type="file", modified_time=self.mtime.get(p, 0.0)))
        return entries

    def read(self, path, format="bytes"):
        return self.store[path]

    def remove(self, path, **kwargs):
        self.store.pop(path, None)
        self.mtime.pop(path, None)


class FakeSandbox:
    def __init__(self):
        self.files = FakeFiles()
        self.killed = False

    def run_code(self, code):
        # simulate the code writing a couple of output files
        self.files.write("/home/user/output/result.csv", b"a,b\n1,2\n")
        self.files.write("/home/user/scratch/temp.txt", b"intermediate")
        return FakeExecution()

    def kill(self):
        self.killed = True


# ---------------------------------------------------------------------------
# Tiny in-memory workspace backend (records writes)
# ---------------------------------------------------------------------------
class _SkillDownload:
    def __init__(self, content=None, error=None):
        self.content = content
        self.error = error


class RecordingWorkspace:
    def __init__(self, skills: dict | None = None):
        self.writes: dict[str, bytes] = {}
        # Workspace `skills/` tree served to the executor's lazy staging
        # (workspace path -> bytes); empty by default.
        self.skill_files: dict[str, bytes] = dict(skills or {})
        self.ls_calls = 0

    def ls(self, path=""):
        self.ls_calls += 1
        listing = type("Listing", (), {})()
        listing.error = None
        listing.entries = [{"path": p, "is_dir": False, "size": len(b)} for p, b in self.skill_files.items()]
        return listing

    def download_files(self, paths):
        return [
            _SkillDownload(content=self.skill_files.get(p), error=None if p in self.skill_files else "file_not_found")
            for p in paths
        ]

    def write(self, path, content):
        self.writes[path.lstrip("/")] = content if isinstance(content, bytes) else content.encode()

    def read(self, path, offset=0, limit=2000):
        class R:
            error = None
            file_data = {"content": "", "encoding": "utf-8"}

        return R()


def make_executor(monkeypatch, workspace=None):
    exe = E2bCodeExecutor(minio={}, api_key="k", keep_sandbox=True)
    # inject the fake sandbox directly, bypassing real init
    exe.sandbox = FakeSandbox()
    exe.sandbox_file_cache = {}
    if workspace is not None:
        exe.workspace_backend = workspace
    return exe


# ---------------------------------------------------------------------------
# copy-out: new_files enumerated + products written to workspace
# ---------------------------------------------------------------------------
def test_copy_out_enumerates_new_files(monkeypatch):
    ws = RecordingWorkspace()
    exe = make_executor(monkeypatch, workspace=ws)
    result = exe.run("print('hi')")

    assert "new_files" in result
    paths = {f["path"]: f for f in result["new_files"]}
    # both produced files captured
    assert any("result.csv" in p for p in paths)
    assert any("temp.txt" in p for p in paths)
    # output/ prefix => is_product True; scratch/ => False
    out = next(f for p, f in paths.items() if "result.csv" in p)
    scr = next(f for p, f in paths.items() if "temp.txt" in p)
    assert out["is_product"] is True
    assert scr["is_product"] is False
    assert out["size"] > 0


def test_copy_out_writes_products_to_workspace(monkeypatch):
    ws = RecordingWorkspace()
    exe = make_executor(monkeypatch, workspace=ws)
    exe.run("print('hi')")

    # output file written under output/, scratch under scratch/
    written = ws.writes
    assert any(k.startswith("output/") and k.endswith("result.csv") for k in written)
    assert any(k.startswith("scratch/") and k.endswith("temp.txt") for k in written)


def test_copy_out_recopies_modified_files(monkeypatch):
    """A file MODIFIED after its first write must reach the workspace again.

    2026-10-15: the worksheet task delivered a 48KB / 11-sheet workbook (file_dir
    is refreshed per run by ``sync_files_to_local``) while the workspace kept the
    7.9KB cover-only version from the file's first write, because ``_copy_out``
    skipped every path in the pre-run snapshot. Cross-turn seeding reads the
    workspace, so the follow-up ("转成ppt") received the stale stub.
    """
    ws = RecordingWorkspace()
    exe = make_executor(monkeypatch, workspace=ws)
    path = "/home/user/output/report.xlsx"

    exe.sandbox.files.write(path, b"v1-cover-only")
    exe.sandbox.files.mtime[path] = 100.0
    pre = exe._snapshot_paths()

    # the model's next call rewrites the same path with the full workbook
    exe.sandbox.files.write(path, b"v2-eleven-sheets")
    exe.sandbox.files.mtime[path] = 101.0

    new_files = exe._copy_out(pre)

    assert [f["path"] for f in new_files] == ["output/report.xlsx"]
    assert ws.writes["output/report.xlsx"] == b"v2-eleven-sheets"


def test_copy_out_still_skips_untouched_snapshot_files(monkeypatch):
    """Files staged by copy-in (or left alone by the run) are not re-pushed."""
    ws = RecordingWorkspace()
    exe = make_executor(monkeypatch, workspace=ws)
    path = "/home/user/uploads/input.csv"

    exe.sandbox.files.write(path, b"unchanged")
    pre = exe._snapshot_paths()

    assert exe._copy_out(pre) == []
    assert ws.writes == {}


# ---------------------------------------------------------------------------
# workspace → sandbox delta sync (the sandbox is disposable, the workspace is home)
# ---------------------------------------------------------------------------
def _sync_executor(monkeypatch, tmp_path, workspace=None):
    exe = make_executor(monkeypatch, workspace=workspace or RecordingWorkspace())
    exe.local_sync_path = str(tmp_path)
    return exe


def test_workspace_sync_pushes_new_and_changed_files(monkeypatch, tmp_path):
    """write_file → run must work: local files reach the sandbox before the run.

    2026-10-15: the model wrote scratch/gen.js with write_file, the sandbox never
    saw it (the two live on different machines), and the turn spun on identical
    existence checks until it was terminated.
    """
    from unittest.mock import Mock

    exe = _sync_executor(monkeypatch, tmp_path)
    (tmp_path / "scratch").mkdir()
    script = tmp_path / "scratch" / "gen.js"
    script.write_text("v1")

    exe._sync_workspace_to_sandbox()
    assert exe.sandbox.files.store["/home/user/scratch/gen.js"] == b"v1"
    assert "scratch/gen.js" in exe._ws_synced

    # Unchanged → no re-push on the next run.
    write_mock = Mock(wraps=exe.sandbox.files.write)
    exe.sandbox.files.write = write_mock
    exe._sync_workspace_to_sandbox()
    assert not any(call.args and str(call.args[0]).endswith("scratch/gen.js") for call in write_mock.call_args_list)

    # Changed content → pushed again.
    script.write_text("v2")
    # The delta key is (mtime_ns, size) and both contents are 2 bytes — on
    # Windows' coarse timestamp grid the two writes can land on the same tick,
    # which made this test flaky there. Force a distinct stamp.
    stamp = script.stat().st_mtime + 1
    os.utime(script, (stamp, stamp))
    exe._sync_workspace_to_sandbox()
    assert exe.sandbox.files.store["/home/user/scratch/gen.js"] == b"v2"


def test_workspace_sync_deletes_files_removed_locally(monkeypatch, tmp_path):
    """A workspace deletion propagates; sandbox-only files are never touched."""
    exe = _sync_executor(monkeypatch, tmp_path)
    (tmp_path / "scratch").mkdir()
    script = tmp_path / "scratch" / "gen.js"
    script.write_text("v1")
    # A sandbox-only file must survive any sync (we only delete what we pushed).
    exe.sandbox.files.store["/home/user/scratch/keep.txt"] = b"mine"

    exe._sync_workspace_to_sandbox()
    assert "/home/user/scratch/gen.js" in exe.sandbox.files.store

    script.unlink()
    exe._sync_workspace_to_sandbox()

    assert "/home/user/scratch/gen.js" not in exe.sandbox.files.store
    assert exe.sandbox.files.store["/home/user/scratch/keep.txt"] == b"mine"


def test_workspace_sync_restores_files_deleted_by_a_run(monkeypatch, tmp_path):
    """The workspace is home: a file a run deleted in the sandbox comes back."""
    exe = _sync_executor(monkeypatch, tmp_path)
    (tmp_path / "scratch").mkdir()
    (tmp_path / "scratch" / "gen.js").write_text("v1")
    exe._sync_workspace_to_sandbox()

    # A run deletes it in the sandbox; _copy_out records the post-run tree.
    del exe.sandbox.files.store["/home/user/scratch/gen.js"]
    exe._sandbox_after = {entry.path: entry.modified_time for entry in exe._walk_sandbox()}

    exe._sync_workspace_to_sandbox()
    assert exe.sandbox.files.store["/home/user/scratch/gen.js"] == b"v1"


def test_workspace_sync_skips_toolchain_and_oversize(monkeypatch, tmp_path):
    exe = _sync_executor(monkeypatch, tmp_path)
    (tmp_path / "scratch").mkdir()
    (tmp_path / "scratch" / "ok.txt").write_text("ok")
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules" / "junk.js").write_text("junk")
    (tmp_path / ".hidden").write_text("hidden")
    (tmp_path / "big.bin").write_bytes(b"x" * (SIZE_AUTOPUSH + 1))

    exe._sync_workspace_to_sandbox()

    assert exe.sandbox.files.store["/home/user/scratch/ok.txt"] == b"ok"
    assert not any("node_modules" in p for p in exe.sandbox.files.store)
    assert not any("hidden" in p for p in exe.sandbox.files.store)
    assert not any("big.bin" in p for p in exe.sandbox.files.store)
    assert "big.bin" not in exe._ws_synced


def test_workspace_sync_failure_rolls_back_state(monkeypatch, tmp_path):
    """Best-effort: a failed push rolls the tracked state back and never raises —
    the next run retries the whole delta and the current run still executes."""
    from unittest.mock import Mock

    exe = _sync_executor(monkeypatch, tmp_path)
    (tmp_path / "scratch").mkdir()
    (tmp_path / "scratch" / "gen.js").write_text("v1")

    exe.sandbox.files.write = Mock(side_effect=RuntimeError("transport down"))
    exe._sync_workspace_to_sandbox()  # must not raise

    assert exe._ws_synced == {}


# ---------------------------------------------------------------------------
# copy-in: small files auto-pushed, large must be declared
# ---------------------------------------------------------------------------
def test_copy_in_small_file_auto_pushed(monkeypatch):
    ws = RecordingWorkspace()
    exe = make_executor(monkeypatch, workspace=ws)

    pushed = {}

    def fake_materialize():
        # working set: one small file from the workspace
        return {"uploads/doc/index.md": b"small content"}

    exe._materialize_working_set = fake_materialize  # type: ignore[attr-defined]
    exe.run("print(1)", required_files=[])

    # small file auto-pushed into sandbox
    assert any("uploads/doc/index.md" in p for p in exe.sandbox.files.store)


def test_copy_in_large_undeclared_hint(monkeypatch):
    ws = RecordingWorkspace()
    exe = make_executor(monkeypatch, workspace=ws)

    big = b"x" * (SIZE_AUTOPUSH + 10)

    def fake_materialize():
        return {"uploads/big/index.md": big}

    exe._materialize_working_set = fake_materialize  # type: ignore[attr-defined]
    result = exe.run("print(1)", required_files=[])

    # big file NOT auto-pushed; a hint is surfaced
    assert not any("uploads/big/index.md" in p for p in exe.sandbox.files.store)
    assert "required_files" in (result.get("copy_in_hint") or "")


def test_copy_in_large_declared_pushed(monkeypatch):
    ws = RecordingWorkspace()
    exe = make_executor(monkeypatch, workspace=ws)

    big = b"x" * (SIZE_AUTOPUSH + 10)

    def fake_materialize():
        return {"uploads/big/index.md": big}

    exe._materialize_working_set = fake_materialize  # type: ignore[attr-defined]
    exe.run("print(1)", required_files=["uploads/big/index.md"])

    assert any("uploads/big/index.md" in p for p in exe.sandbox.files.store)


def test_copy_in_md5_hit_skips_repush(monkeypatch):
    ws = RecordingWorkspace()
    exe = make_executor(monkeypatch, workspace=ws)

    def fake_materialize():
        return {"uploads/doc/index.md": b"same content"}

    exe._materialize_working_set = fake_materialize  # type: ignore[attr-defined]
    exe.run("print(1)", required_files=[])
    writes_count_1 = len(exe._pushed_md5)

    exe.run("print(1)", required_files=[])
    # same content -> md5 hit, not re-tracked as a new push
    assert len(exe._pushed_md5) == writes_count_1


# ---------------------------------------------------------------------------
# run() backward compatible without required_files
# ---------------------------------------------------------------------------
def test_run_without_required_files(monkeypatch):
    exe = make_executor(monkeypatch, workspace=RecordingWorkspace())
    result = exe.run("print(1)")
    assert "results" in result
    assert "new_files" in result


def test_thresholds_exposed():
    assert SIZE_AUTOPUSH > 0
    assert SIZE_INLINE > 0


def test_copy_out_walks_real_directory_shape(monkeypatch):
    from unittest.mock import Mock

    workspace = RecordingWorkspace()
    exe = make_executor(monkeypatch, workspace=workspace)
    exe.sandbox.files.list = Mock(
        side_effect=lambda path: {
            "./": [FakeEntry("output", "/home/user/output", "dir"), FakeEntry(".cache", "/home/user/.cache", "dir")],
            "/home/user/output": [FakeEntry("monthly.csv", "/home/user/output/monthly.csv", "file")],
        }[path]
    )
    exe.sandbox.files.read = Mock(return_value=b"month,amount\n2026-01,200\n")
    products = exe._copy_out({})
    assert products[0]["path"] == "output/monthly.csv"
    assert workspace.writes["output/monthly.csv"] == b"month,amount\n2026-01,200\n"
    assert exe.sandbox.files.list.call_count == 2


def test_sandbox_walk_skips_toolchain_trees(monkeypatch):
    """node_modules / cache trees are neither walked nor harvested.

    A docx/npm task (2026-10-14) spent ~14 min of E2B file-API calls per run
    crawling package-manager trees living in the sandbox home and then uploaded
    470+ junk files; ``_walk_sandbox`` / ``_copy_out`` must prune them.
    """
    from unittest.mock import Mock

    workspace = RecordingWorkspace()
    exe = make_executor(monkeypatch, workspace=workspace)
    exe.sandbox.files.list = Mock(
        side_effect=lambda path: {
            "./": [
                FakeEntry("output", "/home/user/output", "dir"),
                FakeEntry("node_modules", "/home/user/node_modules", "dir"),
                FakeEntry("__pycache__", "/home/user/__pycache__", "dir"),
                FakeEntry("pkg.json", "/home/user/pkg.json", "file"),
            ],
            "/home/user/output": [FakeEntry("report.md", "/home/user/output/report.md", "file")],
        }[path]
    )
    exe.sandbox.files.read = Mock(return_value=b"hello")

    products = exe._copy_out({})

    assert sorted(p["path"] for p in products) == ["output/report.md", "pkg.json"]
    listed = [call.args[0] for call in exe.sandbox.files.list.call_args_list]
    assert "/home/user/node_modules" not in listed
    assert "/home/user/__pycache__" not in listed
    assert "output/report.md" in workspace.writes


def test_local_sync_skips_toolchain_trees(tmp_path):
    from unittest.mock import Mock

    exe = make_executor(None)
    exe.local_sync_path = str(tmp_path)
    exe.sandbox.files.list = Mock(return_value=[FakeEntry("index.js", "/home/user/node_modules/index.js", "file")])

    exe.sync_files_to_local([FakeEntry("node_modules", "/home/user/node_modules", "dir")])

    exe.sandbox.files.list.assert_not_called()
    assert not list(tmp_path.rglob("*"))


def test_local_sync_skips_content_equal_rewrites(tmp_path):
    """An unchanged sandbox file must not be rewritten locally.

    Rewriting bumped the local mtime; the per-run workspace→sandbox sync read
    that as "changed" and pushed the file back, whose write bumped the sandbox
    mtime and triggered another download — every file, every run. Content-equal
    downloads leave the local file (and its mtime) untouched, and the cache entry
    is refreshed so the same listing is not processed twice.
    """
    from unittest.mock import Mock

    exe = make_executor(None)
    exe.local_sync_path = str(tmp_path)
    target = tmp_path / "scratch" / "gen.js"
    target.parent.mkdir()
    target.write_bytes(b"same")
    os.utime(target, (1_000_000, 1_000_000))
    before = target.stat().st_mtime_ns

    entry = FakeEntry("gen.js", "/home/user/scratch/gen.js", "file", modified_time=2000.0)
    exe.sandbox.files.store["/home/user/scratch/gen.js"] = b"same"
    exe.sync_files_to_local([entry])

    assert target.stat().st_mtime_ns == before  # not rewritten — no ping-pong
    assert exe.sandbox_file_cache["/home/user/scratch/gen.js"] is entry

    # A genuinely changed file is still rewritten.
    entry_changed = FakeEntry("gen.js", "/home/user/scratch/gen.js", "file", modified_time=3000.0)
    exe.sandbox.files.store["/home/user/scratch/gen.js"] = b"changed"
    exe.sync_files_to_local([entry_changed])
    assert target.read_bytes() == b"changed"

    # The refreshed cache makes the same listing a no-op on the next run.
    exe.download_file = Mock()
    exe.sync_files_to_local([entry_changed])
    exe.download_file.assert_not_called()


# ---------------------------------------------------------------------------
# Sandbox-lifetime recovery (2026-10-15): keep-alive + recreate-on-gone
# ---------------------------------------------------------------------------
class FakeSandboxNotFound(Exception):
    """Mimics e2b's SandboxNotFoundException message shape."""


def test_run_refreshes_sandbox_timeout(monkeypatch):
    exe = make_executor(monkeypatch)
    calls: list[int] = []
    exe.sandbox.set_timeout = lambda t: calls.append(t)

    exe.run("print(1)")

    assert calls == [exe.timeout]
    # Default bumped from 300s so an ordinary think-gap cannot outlive it.
    assert exe.timeout >= 900


def test_sandbox_gone_recreates_and_retries(monkeypatch):
    exe = make_executor(monkeypatch)
    dead = exe.sandbox

    def _boom(*_args, **_kwargs):
        raise FakeSandboxNotFound("The sandbox was not found: This error is likely due to sandbox timeout.")

    dead.files.list = _boom  # the pre-run snapshot hits this first

    fresh = FakeSandbox()
    monkeypatch.setattr(exe, "init_sandbox", lambda: setattr(exe, "sandbox", fresh))

    result = exe.run("print('hi')")

    assert exe.sandbox is fresh
    assert dead.killed is True  # dead handle cleaned up on reset
    assert any("result.csv" in f["path"] for f in result["new_files"])  # retried once


def test_sandbox_gone_retry_is_single_shot(monkeypatch):
    exe = make_executor(monkeypatch)

    def _boom(*_args, **_kwargs):
        raise FakeSandboxNotFound("The sandbox was not found")

    exe.sandbox.files.list = _boom
    fresh = FakeSandbox()
    fresh.files.list = _boom
    monkeypatch.setattr(exe, "init_sandbox", lambda: setattr(exe, "sandbox", fresh))

    with pytest.raises(FakeSandboxNotFound):
        exe.run("print(1)")


@pytest.mark.parametrize("failure_phase", ["execute", "parse", "copy_out"])
def test_sandbox_loss_after_execution_starts_never_replays_code(monkeypatch, failure_phase):
    from unittest.mock import Mock

    exe = make_executor(monkeypatch)
    execute = Mock(wraps=exe.sandbox.run_code)
    exe.sandbox.run_code = execute
    recreate = Mock()
    monkeypatch.setattr(exe, "init_sandbox", recreate)
    error = FakeSandboxNotFound("The sandbox was not found")
    if failure_phase == "execute":
        execute.side_effect = error
    else:
        monkeypatch.setattr(exe, "parse_results" if failure_phase == "parse" else "_copy_out", Mock(side_effect=error))

    with pytest.raises(FakeSandboxNotFound):
        exe.run("external_side_effect()")

    execute.assert_called_once()
    recreate.assert_not_called()


def test_unrelated_errors_still_raise(monkeypatch):
    exe = make_executor(monkeypatch)

    def _boom(_code):
        raise ValueError("boom")

    exe.sandbox.run_code = _boom

    with pytest.raises(ValueError):
        exe.run("print(1)")


def test_close_tolerates_dead_sandbox(monkeypatch):
    exe = make_executor(monkeypatch)

    def _boom():
        raise FakeSandboxNotFound("The sandbox was not found")

    exe.sandbox.kill = _boom

    exe.close()  # must not raise

    assert exe.sandbox is None


# ---------------------------------------------------------------------------
# Skill staging into the sandbox (2026-10-15): the model is told by SKILL.md to
# run the selected skill's scripts with the code interpreter, so the workspace
# `skills/` tree must actually exist inside the sandbox.
# ---------------------------------------------------------------------------
def test_skills_staged_into_sandbox_on_first_run(monkeypatch):
    ws = RecordingWorkspace(
        skills={
            "/skills/xlsx/SKILL.md": b"# xlsx skill",
            "/skills/xlsx/scripts/build.py": b"print('x')",
        }
    )
    exe = make_executor(monkeypatch, workspace=ws)

    exe.run("print(1)")

    assert "skills/xlsx/SKILL.md" in exe.sandbox.files.store
    assert "skills/xlsx/scripts/build.py" in exe.sandbox.files.store
    ls_calls_after_first = ws.ls_calls
    assert ls_calls_after_first == 1

    exe.run("print(2)")
    assert ws.ls_calls == ls_calls_after_first  # staged once per sandbox


def test_skills_restaged_after_sandbox_recreated(monkeypatch):
    ws = RecordingWorkspace(skills={"/skills/xlsx/SKILL.md": b"# x"})
    exe = make_executor(monkeypatch, workspace=ws)
    exe.run("print(1)")

    # Simulate run()'s recreate path: the dead handle is reset and a fresh
    # sandbox is installed (init_sandbox is patched because tests never spin a
    # real one).
    monkeypatch.setattr(exe, "init_sandbox", lambda: setattr(exe, "sandbox", FakeSandbox()))
    exe._reset_sandbox()
    exe.run("print(2)")

    assert "skills/xlsx/SKILL.md" in exe.sandbox.files.store
    assert ws.ls_calls == 2


def test_no_skills_means_no_staging(monkeypatch):
    ws = RecordingWorkspace()
    exe = make_executor(monkeypatch, workspace=ws)

    result = exe.run("print(1)")

    assert not any(p.startswith("skills/") for p in exe.sandbox.files.store)
    assert result["new_files"]  # the run itself is unaffected
