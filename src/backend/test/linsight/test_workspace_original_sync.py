"""Cross-turn originals must reach the LOCAL task dir, not just the workspace.

``_init_file_directory`` prefetches originals from THIS turn's ``files``, which is
empty on a follow-up ("把刚才那个表再算一下") — there, the original only exists in
the workspace, put there by ``seed_workspace_from_previous``. But the code
interpreter's file list is built from ``os.walk(file_dir)`` / ``local_sync_path``:
it never talks to MinIO. Without the sync step the model sees ``uploads/x.xlsx``
in ``ls``, is told by the pointer block it can compute on it, and then finds
nothing in the sandbox — the whole point of carrying the original is lost.

Same contract for the previous turn's ``output/`` deliverables: the seed copies
them into this turn's workspace (``ls``/``read_file`` see them), but a follow-up
that must RUN on them ("转成ppt") needs them in ``file_dir`` too. 2026-10-15: the
xlsx from the previous turn was visible in ``ls`` yet absent in Python, and the
model scanned ``/root/.cache`` 15+ times printing ``0`` until it was terminated.

Second invariant: the sync lands files AFTER ``_init_file_directory`` took the
deliverable baseline, so they must be added to it. Otherwise the user's own upload
comes back as "the agent produced this".

``asyncio_mode = auto`` — async tests need no decorator.
"""

import hashlib
import os
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from bisheng.linsight.domain.task_exec import LinsightWorkflowTask

SVID = "sv-followup"


class _Obj:
    def __init__(self, name: str, size: int = 10):
        self.object_name = name
        self.size = size
        self.is_dir = False


def _minio(objects: dict[str, bytes]) -> MagicMock:
    """MinIO stand-in whose listing and reads are backed by one dict."""
    minio = MagicMock()
    minio.bucket = "bisheng"

    def _list(bucket, prefix="", recursive=True):
        return iter([_Obj(k, len(v)) for k, v in objects.items() if k.startswith(prefix)])

    # WorkspaceBackend.ls calls ``minio.list_objects_sync`` — stubbing only
    # ``minio_client_sync.list_objects`` made every listing come back empty and
    # the whole suite silently asserted nothing.
    minio.list_objects_sync.side_effect = _list
    minio.get_object_sync.side_effect = lambda bucket_name=None, object_name=None: objects.get(object_name)
    return minio


def _task(file_dir: str) -> LinsightWorkflowTask:
    task = LinsightWorkflowTask.__new__(LinsightWorkflowTask)
    task.file_dir = file_dir
    task._baseline_files = set()
    return task


async def test_original_synced_from_workspace_and_baselined(tmp_path):
    objects = {
        f"workspace/{SVID}/uploads/销售数据.md": b"# view\n",
        f"workspace/{SVID}/uploads/销售数据.xlsx": b"PK\x03\x04binary",
    }
    task = _task(str(tmp_path))

    with patch(
        "bisheng.linsight.domain.task_exec.get_minio_storage",
        AsyncMock(return_value=_minio(objects)),
    ):
        await task._sync_workspace_originals(SimpleNamespace(id=SVID))

    local = tmp_path / "uploads" / "销售数据.xlsx"
    assert local.read_bytes() == b"PK\x03\x04binary"
    # The markdown view is read through read_file (MinIO); no local copy needed.
    assert not (tmp_path / "uploads" / "销售数据.md").exists()
    # ...and it must not be mistaken for this turn's deliverable.
    assert str(local) in {os.path.normpath(p) for p in task._baseline_files}


async def test_sync_is_idempotent_for_already_prefetched_files(tmp_path):
    """Fresh turns already prefetched the original; the sync must not re-download."""
    (tmp_path / "uploads").mkdir()
    (tmp_path / "uploads" / "data.xlsx").write_bytes(b"already here")
    objects = {f"workspace/{SVID}/uploads/data.xlsx": b"different bytes"}
    task = _task(str(tmp_path))
    minio = _minio(objects)

    with patch("bisheng.linsight.domain.task_exec.get_minio_storage", AsyncMock(return_value=minio)):
        await task._sync_workspace_originals(SimpleNamespace(id=SVID))

    assert (tmp_path / "uploads" / "data.xlsx").read_bytes() == b"already here"
    minio.get_object_sync.assert_not_called()
    assert task._baseline_files == set()


async def test_sync_failure_never_blocks_the_turn(tmp_path):
    """Best-effort: losing the precise-data track must not fail the task."""
    task = _task(str(tmp_path))

    with patch(
        "bisheng.linsight.domain.task_exec.get_minio_storage",
        AsyncMock(side_effect=RuntimeError("minio down")),
    ):
        await task._sync_workspace_originals(SimpleNamespace(id=SVID))  # must not raise

    assert task._baseline_files == set()


async def test_previous_turn_output_synced_for_code_interpreter(tmp_path):
    """The previous turn's ``output/`` deliverables must reach ``file_dir``.

    They are seeded into the workspace (so ``ls``/``read_file`` show them), but the
    code interpreter only reads the local ``file_dir``: without this the model sees
    ``output/report.xlsx`` in ``ls`` and gets FileNotFoundError from Python — then
    hunts the sandbox for it instead of doing the follow-up.
    """
    objects = {
        f"workspace/{SVID}/output/全球二手车市场行情研究报告.xlsx": b"PK\x03\x04prev",
        f"workspace/{SVID}/output/概要.md": b"# prior deliverable md\n",
    }
    task = _task(str(tmp_path))

    with patch(
        "bisheng.linsight.domain.task_exec.get_minio_storage",
        AsyncMock(return_value=_minio(objects)),
    ):
        await task._sync_workspace_originals(SimpleNamespace(id=SVID))

    # Both the workbook and the markdown deliverable land locally. Unlike
    # uploads/, the ``.md`` is NOT skipped: it is a deliverable the follow-up may
    # need to parse in code (e.g. build slides from it).
    xlsx = tmp_path / "output" / "全球二手车市场行情研究报告.xlsx"
    md = tmp_path / "output" / "概要.md"
    assert xlsx.read_bytes() == b"PK\x03\x04prev"
    assert md.read_bytes() == b"# prior deliverable md\n"
    # ...and neither may come back to the user as THIS turn's deliverable.
    baseline = {os.path.normpath(p) for p in task._baseline_files}
    assert str(xlsx) in baseline
    assert str(md) in baseline
    # The staged output/ content is remembered (path -> md5) so the completion
    # scan can tell "inherited and untouched" from "produced this run".
    assert task._synced_output_md5 == {
        os.path.normpath(str(xlsx)): hashlib.md5(b"PK\x03\x04prev").hexdigest(),
        os.path.normpath(str(md)): hashlib.md5(b"# prior deliverable md\n").hexdigest(),
    }


async def test_output_sync_survives_uploads_listing_failure(tmp_path):
    """A failed ``uploads/`` listing must not keep ``output/`` out of the sandbox."""
    objects = {f"workspace/{SVID}/output/report.xlsx": b"PK\x03\x04"}
    minio = _minio(objects)

    def _list(bucket, prefix="", recursive=True):
        if "/uploads/" in prefix:
            raise RuntimeError("listing hiccup")
        return iter([_Obj(k, len(v)) for k, v in objects.items() if k.startswith(prefix)])

    minio.minio_client_sync.list_objects.side_effect = _list
    task = _task(str(tmp_path))

    with patch(
        "bisheng.linsight.domain.task_exec.get_minio_storage",
        AsyncMock(return_value=minio),
    ):
        await task._sync_workspace_originals(SimpleNamespace(id=SVID))

    assert (tmp_path / "output" / "report.xlsx").read_bytes() == b"PK\x03\x04"


async def test_inherited_output_not_redelivered_unless_rewritten(tmp_path):
    """A read-only follow-up must not headline the file it merely inherited.

    ``select_deliverables`` treats every ``output/`` file as this run's product,
    so the completion scan subtracts files that are still byte-identical to the
    staged previous-turn copy. One the agent rewrote IS this run's deliverable
    again — its md5 changed.
    """
    out = tmp_path / "output"
    out.mkdir()
    inherited = out / "全球二手车市场行情研究报告.xlsx"
    inherited.write_bytes(b"PK\x03\x04prev")
    fresh = out / "probe_copy.csv"
    fresh.write_bytes(b"a,1\n")

    task = _task(str(tmp_path))
    task._synced_output_md5 = {
        os.path.normpath(str(inherited)): hashlib.md5(b"PK\x03\x04prev").hexdigest()
    }

    details = await task._read_deliverable_file_details()
    names = sorted(d["file_name"] for d in details)
    assert names == ["probe_copy.csv"]

    # Rewriting the workbook lifts the exclusion — it is this run's output now.
    inherited.write_bytes(b"PK\x03\x04rewritten")
    details = await task._read_deliverable_file_details()
    assert sorted(d["file_name"] for d in details) == [
        "probe_copy.csv",
        "全球二手车市场行情研究报告.xlsx",
    ]
