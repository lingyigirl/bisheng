"""Artifact harvesting policy for code-interpreter workspaces.

The policy separates metadata discovery from content transfer. Unknown files
remain durable so sandbox recreation cannot lose task state. Only explicit,
regenerable scratch byproducts may stay inside the disposable sandbox.
"""

from __future__ import annotations

import os

import pytest

from bisheng_langchain.gpts.tools.code_interpreter.artifact_policy import (
    ArtifactDisposition,
    classify_workspace_path,
)
from bisheng_langchain.gpts.tools.code_interpreter.local_executor import LocalExecutor


@pytest.mark.parametrize(
    "path",
    [
        "output/report.pdf",
        "output/node_modules/package/data.json",
        "output/scratch/fonts/font.otf",
        "/home/user/output/report.pptx",
        "D:/task/output/report.xlsx",
    ],
)
def test_output_is_always_persisted(path):
    decision = classify_workspace_path(path)

    assert decision.disposition is ArtifactDisposition.PERSIST
    assert decision.is_product is True


@pytest.mark.parametrize(
    "path",
    [
        "scratch/fonts/font.otf",
        "scratch/build/generated.js",
        "scratch/project/target/debug/app",
        "scratch/coverage/index.html",
        "scratch/test-results/result.json",
        "scratch/playwright-report/index.html",
        "scratch/report.aux",
        "scratch/report.synctex.gz",
        "scratch/data.tmp",
        "scratch/data.part",
        "scratch/~$report.docx",
        "/home/user/scratch/fonts/font.otf",
        "D:/task/scratch/build/generated.js",
    ],
)
def test_explicit_scratch_byproducts_stay_in_sandbox(path):
    decision = classify_workspace_path(path)

    assert decision.disposition is ArtifactDisposition.SANDBOX_ONLY
    assert decision.is_product is False


@pytest.mark.parametrize(
    "path",
    [
        "scratch/model.bin",
        "scratch/large-dataset.parquet",
        "scratch/report.log",
        "scratch/archive.zip",
        "scratch/private-font-name-without-extension",
        "uploads/source.xlsx",
        "skills/demo/assets/template.docx",
        "root-level.bin",
    ],
)
def test_unknown_files_are_always_persisted(path):
    decision = classify_workspace_path(path)

    assert decision.disposition is ArtifactDisposition.PERSIST
    assert decision.is_product is False


@pytest.mark.parametrize(
    "path",
    [
        "node_modules/package/index.js",
        "scratch/node_modules/package/index.js",
        "__pycache__/module.pyc",
        ".cache/models/model.bin",
        "skill/.venv/lib/python.py",
        "repo/.git/objects/object",
    ],
)
def test_toolchain_paths_are_pruned_before_content_read(path):
    decision = classify_workspace_path(path)

    assert decision.disposition is ArtifactDisposition.PRUNE
    assert decision.is_product is False


def test_local_executor_snapshot_uses_the_same_artifact_policy(tmp_path):
    (tmp_path / "output").mkdir()
    (tmp_path / "output" / "report.md").write_text("report")
    build = tmp_path / "scratch" / "build"
    build.mkdir(parents=True)
    (build / "cache.bin").write_bytes(b"cache")
    fonts = tmp_path / "scratch" / "fonts"
    fonts.mkdir()
    (fonts / "font.otf").write_bytes(b"font")
    (tmp_path / "scratch" / "model.bin").write_bytes(b"model")

    snapshot = LocalExecutor(minio={})._snapshot_files(str(tmp_path))

    assert set(snapshot) == {
        os.path.join("output", "report.md"),
        os.path.join("scratch", "model.bin"),
    }
