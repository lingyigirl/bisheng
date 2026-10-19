"""Task report tools backed by the same verified renderer as Save As."""

import os
from typing import Any

from langchain_core.tools import BaseTool
from loguru import logger
from pydantic import BaseModel, Field

from bisheng.linsight.domain.services.report_export import export_workspace_report, workspace_path


class _ExportInput(BaseModel):
    source_path: str = Field(..., description="工作区 output/ 下的源 markdown 路径, 例如 output/report.md")
    dest_path: str = Field(default="", description="目标文件路径; 留空则自动用源文件名替换扩展名")


class _ExportToolBase(BaseTool):
    backend: Any = None
    args_schema: type[BaseModel] = _ExportInput
    target: str = ""

    def _run(self, *args, **kwargs) -> str:
        return "not supported in sync mode, please use async version"

    async def _arun(self, source_path: str, dest_path: str = "", **kwargs) -> str:
        if not hasattr(self.backend, "awrite"):
            return "导出不可用: 当前工作区不支持文件写入。"
        try:
            destination = workspace_path(
                dest_path or os.path.splitext(source_path)[0] + "." + self.target, getattr(self.backend, "svid", "")
            )
            if not destination.startswith("output/") or not destination.endswith("." + self.target):
                raise ValueError("Export destination must use the target extension under output/")
            data, manifest = await export_workspace_report(self.backend, source_path, self.target)
            result = await self.backend.awrite(destination, data)
            if getattr(result, "error", None):
                raise RuntimeError(result.error)
            label = "Word" if self.target == "docx" else "PDF"
            return f"已生成 {label} 文档: {result.path}; 结构核验: {manifest}"
        except Exception as exc:
            # Tools must return a recoverable failure; the agent can correct the source.
            logger.exception("{} export failed", self.target)
            return f"导出失败: {exc}"


class ExportDocxTool(_ExportToolBase):
    name: str = "export_docx"
    target: str = "docx"
    description: str = (
        "把 output/ 下已写好的 Markdown 导出为 Word(.docx)。图片必须位于当前工作区。仅在用户要求 Word 时调用。"
    )


class ExportPdfTool(_ExportToolBase):
    name: str = "export_pdf"
    target: str = "pdf"
    description: str = "把 output/ 下已写好的 Markdown 导出为 PDF。图片必须位于当前工作区。仅在用户要求 PDF 时调用。"


def init_linsight_export_tools(backend) -> list[BaseTool]:
    if backend is None or not hasattr(backend, "awrite"):
        return []
    return [ExportDocxTool(backend=backend), ExportPdfTool(backend=backend)]
