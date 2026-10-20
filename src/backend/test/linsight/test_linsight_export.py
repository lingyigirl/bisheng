"""覆盖 AC: AC-08-AC-19 - tool integration never advertises failed files."""

import io
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from docx import Document
from PIL import Image

from bisheng.linsight.domain.services import report_export
from bisheng.tool.domain.langchain import linsight_export
from bisheng.tool.domain.services.report_runtime import ReportRuntime


@pytest.fixture
def backend():
    image = io.BytesIO()
    Image.new("RGB", (100, 60), "blue").save(image, format="PNG")
    files = {"output/report.md": b"# Hello\n\n![Chart](chart.png)", "output/chart.png": image.getvalue()}

    async def download(paths):
        return [
            SimpleNamespace(content=files.get(path), error=None if path in files else "not found") for path in paths
        ]

    return SimpleNamespace(
        svid="test-task",
        adownload_files=download,
        awrite=AsyncMock(return_value=SimpleNamespace(path="/output/report.docx", error=None)),
    )


def test_init_and_sync_mode(backend):
    assert linsight_export.init_linsight_export_tools(None) == []
    assert linsight_export.init_linsight_export_tools(object()) == []
    assert len(linsight_export.init_linsight_export_tools(backend)) == 2
    assert "not supported" in linsight_export.ExportPdfTool(backend=backend)._run()


async def test_tool_produces_real_embedded_docx(backend, tmp_path, monkeypatch):
    from bisheng.tool.domain.services.report_worker import main

    async def execute(directory):
        main(directory)

    monkeypatch.setattr(report_export, "runtime", ReportRuntime(tmp_path, executor=execute))
    result = await linsight_export.ExportDocxTool(backend=backend)._arun("output/report.md")
    assert "已生成 Word" in result
    args = backend.awrite.await_args.args
    assert args[0] == "output/report.docx"
    assert len(Document(io.BytesIO(args[1])).inline_shapes) == 1


async def test_missing_image_and_invalid_destination_do_not_write(backend):
    result = await linsight_export.ExportDocxTool(backend=backend)._arun("output/missing.md")
    assert "导出失败" in result
    result = await linsight_export.ExportDocxTool(backend=backend)._arun("output/report.md", "../other.docx")
    assert "导出失败" in result
    backend.awrite.assert_not_awaited()


async def test_runtime_failure_is_recoverable(backend, monkeypatch):
    monkeypatch.setattr(report_export.runtime, "convert", AsyncMock(side_effect=RuntimeError("renderer missing")))
    result = await linsight_export.ExportPdfTool(backend=backend)._arun("output/report.md")
    assert "导出失败" in result
    backend.awrite.assert_not_awaited()


@pytest.mark.parametrize(
    "path",
    [
        "../secret.png",
        "/etc/passwd",
        "workspace/other/output/a.png",
        "https://example.com/x.png",
        "file:///C:/x.png",
        "C:\\temp\\x.png",
    ],
)
def test_report_resource_path_rejects_escape(path):
    with pytest.raises(ValueError):
        report_export.workspace_path(path, "test-task", source="output/report.md")


def test_report_resource_path_allows_parent_inside_workspace():
    assert (
        report_export.workspace_path("../uploads/中文 图.png", "task", source="output/report.md")
        == "uploads/中文 图.png"
    )
