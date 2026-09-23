#!/usr/bin/env python3
"""使用本地财务报表 OCR JSON 检查 Adapter 之后的业务链路。"""

import argparse
import asyncio
import json
from pathlib import Path
from typing import Any

from langchain_core.documents import Document

from bisheng.knowledge.domain.services.knowledge_utils import KnowledgeUtils
from bisheng.knowledge.domain.schemas.knowledge_rag_schema import Metadata
from bisheng.knowledge.rag.pipeline.base import NormalPipeline
from bisheng.knowledge.rag.pipeline.loader.financial_report_ocr import FinancialReportOcrLoader
from bisheng.knowledge.rag.pipeline.loader.utils.financial_report_adapter import (
    financial_report_to_markdown,
)
from bisheng.knowledge.rag.pipeline.transformer.preview_cache import PreviewCacheTransformer
from bisheng.knowledge.rag.pipeline.transformer.splitter import SplitterTransformer


class JsonFixtureLoader:
    """将本地 JSON 转成 Document，模拟 Loader 的出口。"""

    def __init__(self, documents: list[Document]):
        self.documents = documents

    def load(self) -> list[Document]:
        return self.documents

    async def aload(self) -> list[Document]:
        return await asyncio.to_thread(self.load)


class RecordingVectorStore:
    """记录 add_documents 的输入，不写入真实向量库。"""

    def __init__(self):
        self.documents: list[Document] = []

    def add_documents(self, documents: list[Document], **kwargs: Any) -> list[str]:
        self.documents.extend(documents)
        return [str(index) for index in range(len(documents))]

    async def aadd_documents(self, documents: list[Document], **kwargs: Any) -> list[str]:
        return self.add_documents(documents, **kwargs)


def _load_json_payload(path: Path) -> dict[str, Any]:
    """支持纯 JSON，也支持带说明文字的粘贴文本。"""
    text = path.read_text(encoding="utf-8-sig")
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end < start:
        raise ValueError(f"JSON object not found in {path}")
    payload = json.loads(text[start : end + 1])
    if not isinstance(payload, dict):
        raise ValueError("OCR payload must be a JSON object")
    return payload


def _build_document(payload: dict[str, Any]) -> Document:
    markdown = financial_report_to_markdown(payload)
    if not markdown.strip():
        raise ValueError("OCR payload converted to empty Markdown")

    metadata = Metadata(
        document_id=0,
        document_name="financial_report_fixture.pdf",
        knowledge_id=0,
        abstract="财务报表 OCR 联调样例",
        upload_time=0,
        update_time=0,
        uploader="fixture",
        updater="fixture",
        user_metadata={},
    ).model_dump(exclude_none=True)
    return Document(page_content=markdown, metadata=metadata)


def _build_transformers(chunk_size: int, chunk_overlap: int) -> list[Any]:
    return [
        SplitterTransformer(
            separator=["\n\n", "\n", "。", "\."],
            separator_rule=["after", "after", "after", "after"],
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
        ),
        PreviewCacheTransformer(preview_cache_key=None, file_metadata={}),
    ]


def run_test(json_file: Path, chunk_size: int, chunk_overlap: int, show_chunks: int) -> int:
    payload = _load_json_payload(json_file)
    FinancialReportOcrLoader._validate_payload(payload)

    source_document = _build_document(payload)
    vector_store = RecordingVectorStore()
    pipeline = NormalPipeline(
        loader=JsonFixtureLoader([source_document]),
        transformers=_build_transformers(chunk_size, chunk_overlap),
        vector_store=[vector_store],
    )
    result = pipeline.run()

    chunks = result.documents or []
    if not chunks:
        raise AssertionError("pipeline produced no chunks")
    if not vector_store.documents:
        raise AssertionError("pipeline did not call vector store add_documents")

    print("=" * 72)
    print("财务报表 OCR JSON 后续链路测试")
    print("=" * 72)
    print(f"JSON 文件          : {json_file}")
    print(f"OCR results        : {len(payload['data']['results'])}")
    print(f"Markdown 字符数    : {len(source_document.page_content)}")
    print(f"切分后 chunk 数    : {len(chunks)}")
    print(f"向量库接收数       : {len(vector_store.documents)}")
    print(f"Pipeline 阶段       : {result.stage_reached.name}")

    for index, chunk in enumerate(chunks[: max(show_chunks, 0)]):
        content = KnowledgeUtils.split_chunk_metadata(chunk.page_content)
        print("-" * 72)
        print(f"chunk[{index}] metadata={json.dumps(chunk.metadata, ensure_ascii=False)}")
        print(content[:500].replace("\n", "\n"))

    assert len(chunks) == len(vector_store.documents)
    print("=" * 72)
    print("RESULT: PASS")
    print("=" * 72)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="校验财务报表 OCR JSON 转 Markdown 后的现有业务链路。")
    parser.add_argument("--json-file", required=True, type=Path, help="财务报表 OCR JSON 或粘贴文本文件")
    parser.add_argument("--chunk-size", default=1000, type=int, help="切分大小")
    parser.add_argument("--chunk-overlap", default=100, type=int, help="切分重叠大小")
    parser.add_argument("--show-chunks", default=3, type=int, help="打印前 N 个 chunk")
    args = parser.parse_args()

    return run_test(
        json_file=args.json_file,
        chunk_size=args.chunk_size,
        chunk_overlap=args.chunk_overlap,
        show_chunks=args.show_chunks,
    )


if __name__ == "__main__":
    raise SystemExit(main())
