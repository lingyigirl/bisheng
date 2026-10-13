import base64
from itertools import pairwise
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest
from langchain_core.documents import Document

from bisheng.api.v1.schemas import FileProcessBase
from bisheng.common.errcode.knowledge import KnowledgeFileEmptyError
from bisheng.core.config.settings import FinancialReportOcrConf, KnowledgeConf
from bisheng.knowledge.rag.base_file_pipeline import BaseFilePipeline
from bisheng.knowledge.rag.pipeline.loader.financial_report_ocr import FinancialReportOcrLoader
from bisheng.knowledge.rag.pipeline.loader.utils.financial_report_adapter import (
    build_markdown,
    financial_report_to_markdown,
)
from bisheng.utils.exceptions import EtlException


def _sample_payload() -> dict:
    return {
        "e": {"code": 200, "msg": "操作成功", "requestId": "req-1"},
        "cost": 0.01,
        "data": {
            "code": "00000",
            "message": "成功",
            "request_id": "data-req-1",
            "results": [
                {
                    "description": "资产负债表",
                    "page_no": 1,
                    "column_info": ["项目", "金额", "金额"],
                    "data": [
                        [
                            {"score": 1, "words": "货币资金"},
                            {"score": 1, "words": "186,305.92"},
                            {"score": 1, "words": "111,170.88"},
                        ],
                        [
                            {"score": 1, "words": ""},
                            {"score": 1, "words": ""},
                            {"score": 1, "words": ""},
                        ],
                    ],
                }
            ],
        },
    }


def _mock_response(payload: dict) -> Mock:
    response = Mock()
    response.status_code = 200
    response.text = ""
    response.json.return_value = payload
    return response


def test_financial_report_adapter_builds_markdown_table():
    markdown = financial_report_to_markdown(_sample_payload())

    assert "## 资产负债表(第1页)" in markdown
    assert "| 项目 | 金额 | 金额 |" in markdown
    assert "|---|---|---|" in markdown
    assert "| 货币资金 | 186,305.92 | 111,170.88 |" in markdown


def test_financial_report_loader_multipart(tmp_path):
    file_path = tmp_path / "report.pdf"
    file_path.write_bytes(b"pdf")

    with patch(
        "bisheng.knowledge.rag.pipeline.loader.financial_report_ocr.requests.post",
        return_value=_mock_response(_sample_payload()),
    ) as post:
        loader = FinancialReportOcrLoader(
            file_path=str(file_path),
            file_metadata={"document_id": 1, "knowledge_id": 2},
            file_extension="pdf",
            tmp_dir=str(tmp_path),
            url="http://ocr.example/ocr/v1/financial_report",
            request_kwargs={"lang": "zh"},
        )
        documents = loader.load()

    assert len(documents) == 1
    assert "## 资产负债表(第1页)" in documents[0].page_content
    assert documents[0].metadata == {"document_id": 1, "knowledge_id": 2}
    assert post.call_args.kwargs["data"] == {"lang": "zh"}
    assert "files" in post.call_args.kwargs


def test_financial_report_loader_base64_json(tmp_path):
    file_path = tmp_path / "report.png"
    file_path.write_bytes(b"png")

    with patch(
        "bisheng.knowledge.rag.pipeline.loader.financial_report_ocr.requests.post",
        return_value=_mock_response(_sample_payload()),
    ) as post:
        loader = FinancialReportOcrLoader(
            file_path=str(file_path),
            file_metadata={},
            file_extension="png",
            tmp_dir=str(tmp_path),
            url="http://ocr.example/ocr/v1/financial_report",
            request_mode="base64_json",
        )
        loader.load()

    assert post.call_args.kwargs["json"]["file"] == base64.b64encode(b"png").decode("ascii")


def test_financial_report_loader_rejects_business_error():
    payload = {"e": {"code": 500, "msg": "failed"}, "data": {"code": "00000", "results": []}}

    with pytest.raises(EtlException):
        FinancialReportOcrLoader._validate_payload(payload)


def test_financial_report_provider_enables_image_parser():
    conf = KnowledgeConf(
        loader_provider="financial_report_ocr",
        financial_report_ocr=FinancialReportOcrConf(url="http://ocr.example"),
    )
    empty_conf = KnowledgeConf(loader_provider="financial_report_ocr")

    assert conf.image_parser_enabled is True
    assert empty_conf.image_parser_enabled is False


class _DummyPipeline(BaseFilePipeline):
    @property
    def file_metadata(self) -> dict:
        return {}

    def prepare_local_file(self):
        return None


def test_financial_report_provider_routes_to_new_loader(tmp_path):
    pipeline = _DummyPipeline(
        invoke_user_id=1,
        file_name="report.pdf",
        file_rule=FileProcessBase(knowledge_id=1),
    )
    pipeline.local_file_path = str(tmp_path / "report.pdf")
    pipeline.tmp_dir = str(tmp_path)
    conf = KnowledgeConf(
        loader_provider="financial_report_ocr",
        financial_report_ocr=FinancialReportOcrConf(url="http://ocr.example"),
    )

    with patch("bisheng.common.services.config_service.ConfigService.get_knowledge", return_value=conf):
        loader = pipeline._build_pdf_loader(pipeline.local_file_path, "pdf")

    assert isinstance(loader, FinancialReportOcrLoader)


def test_financial_report_provider_requires_url():
    pipeline = _DummyPipeline(
        invoke_user_id=1,
        file_name="report.pdf",
        file_rule=FileProcessBase(knowledge_id=1),
    )
    pipeline.local_file_path = "/tmp/report.pdf"
    pipeline.tmp_dir = "/tmp"
    conf = KnowledgeConf(loader_provider="financial_report_ocr")

    with (
        patch("bisheng.common.services.config_service.ConfigService.get_knowledge", return_value=conf),
        pytest.raises(EtlException),
    ):
        pipeline._build_pdf_loader(pipeline.local_file_path, "pdf")


# --- /ocr/v1/table_multi 响应形状（顶层 results + tables[].cells[]）---


def _sample_table_multi_payload() -> dict:
    """真实 table_multi 响应的精简版：结构与客户返回一致，内容缩短。

    几何值沿用 docs/ocr-resp.json（页 1654×2339 栅格），用于验证坐标换算与分区对齐。
    """
    return {
        "code": "00000",
        "resp_code": "00000",
        "message": "成功",
        "request_id": "req-multi-1",
        "results": [
            {
                "page_no": 0,
                "width": "1654",
                "height": "2339",
                "tables": [
                    {
                        "cells": [
                            {
                                "row_index": 0,
                                "col_index": 0,
                                "cols_span": 5,
                                "rows_span": 1,
                                "words": "客户名称：某公司",
                                "bbox": [323, 286, 1328, 354],
                            },
                            {"row_index": 1, "col_index": 0, "words": "序号", "bbox": [284, 400, 380, 430]},
                            {"row_index": 1, "col_index": 1, "words": "费用项目", "bbox": [380, 400, 560, 430]},
                            {"row_index": 2, "col_index": 0, "words": "1", "bbox": [284, 430, 380, 460]},
                            {"row_index": 2, "col_index": 1, "words": "物业服务费", "bbox": [380, 430, 560, 460]},
                            {"row_index": 2, "col_index": 2, "words": "4565.6", "bbox": [560, 430, 700, 460]},
                        ]
                    }
                ],
                "text": [
                    {
                        "score": 0.93,
                        "words": "不应出现的正文",
                        "position": {"top": 270, "left": 618, "width": 414, "height": 35},
                    }
                ],
            }
        ],
        "async_task_id": "-1",
    }


def test_financial_report_adapter_renders_table_multi():
    markdown = financial_report_to_markdown(_sample_table_multi_payload())

    assert "## 表格" in markdown
    # 宽度由 cols_span 决定（0+5），而非 max(col_index)=2 —— 同时覆盖「跨列扩宽」与「超出末列」
    assert "| 列1 | 列2 | 列3 | 列4 | 列5 |" in markdown
    assert "| 客户名称：某公司 |  |  |  |  |" in markdown
    assert "| 1 | 物业服务费 | 4565.6 |  |  |" in markdown
    # 页面级 text 参与渲染，且排在该页表格之前（切分后首个 chunk 能同时含标题与表头）
    assert "不应出现的正文" in markdown
    assert markdown.index("不应出现的正文") < markdown.index("| 列1 |")
    # 不出页码：该接口 page_no 是 0 基，渲染成「第0页」是用户可见的缺陷。
    # 断言用 "(第" 而非 "第" —— 真实表格内容里本来就会出现「第一天」这类词。
    assert "(第" not in markdown


def _sample_page_text_payload() -> dict:
    """真实 table_multi 响应里的页面级文字（取自 docs/ocr-resp.json）。

    7 条 OCR 碎片 + 6 条有效信息，坐标沿用真实值（末两条在原始响应里就是乱序的）。
    """
    return {
        "results": [
            {
                "page_no": 0,
                "tables": [],
                "text": [
                    {"score": 0.93, "words": "D", "position": {"top": 116, "left": 614}},
                    {"score": 0.87, "words": "11", "position": {"top": 116, "left": 715}},
                    {"score": 0.96, "words": "1V1", "position": {"top": 116, "left": 816}},
                    {"score": 0.97, "words": "S", "position": {"top": 119, "left": 1391}},
                    {"score": 0.95, "words": "-C", "position": {"top": 260, "left": 182}},
                    {
                        "score": 0.99,
                        "words": "厦门地铁住宅物业有限公司收费通知单",
                        "position": {"top": 270, "left": 618},
                    },
                    {"score": 0.83, "words": ")", "position": {"top": 345, "left": 189}},
                    {"score": 0.99, "words": "开票人：", "position": {"top": 1270, "left": 234}},
                    {"score": 0.99, "words": "王珊珊", "position": {"top": 1376, "left": 235}},
                    {"score": 0.99, "words": "电子发票", "position": {"top": 1606, "left": 1211}},
                    {
                        "score": 0.99,
                        "words": "(增值税专用发票)",
                        "position": {"top": 1854, "left": 1202},
                    },
                    {"score": 0.87, "words": "国名：", "position": {"top": 1932, "left": 1178}},
                ],
            }
        ]
    }


def test_page_text_drops_ocr_fragments():
    lines = financial_report_to_markdown(_sample_page_text_payload()).splitlines()

    # 按整行比对而非子串 —— 「(增值税专用发票)」自身就带右括号
    for fragment in ("D", "11", "1V1", "S", "-C", ")", "日"):
        assert fragment not in lines
    # 「国名：」只有 3 字符，但含中文，不算碎片，必须保留
    for kept in (
        "厦门地铁住宅物业有限公司收费通知单",
        "开票人：",
        "王珊珊",
        "电子发票",
        "(增值税专用发票)",
        "国名：",
    ):
        assert kept in lines


def test_page_text_ordered_by_position():
    """text 数组顺序未必是阅读顺序，按 (top, left) 重排。"""
    payload = {
        "results": [
            {
                "page_no": 0,
                "text": [
                    {"words": "页脚信息", "position": {"top": 1932, "left": 1178}},
                    {"words": "页眉标题", "position": {"top": 270, "left": 618}},
                ],
            }
        ]
    }

    assert financial_report_to_markdown(payload).splitlines() == ["页眉标题", "页脚信息"]


def test_page_with_text_but_no_tables_still_renders():
    """只有文字、没有 tables 键的页也要产出内容（曾因 tables 类型检查被整页丢弃）。"""
    payload = {"results": [{"page_no": 0, "text": [{"words": "只有文字没有表格"}]}]}

    assert financial_report_to_markdown(payload) == "只有文字没有表格"


def test_page_text_tolerates_malformed_items():
    """页面级 text 结构异常时不抛异常，有效条目照常渲染。"""
    payload = {
        "results": [
            {
                "page_no": 0,
                "text": [
                    "not-a-dict",
                    {"words": "有效文字"},
                    {"words": "坐标非法", "position": "nope"},
                    {"words": "坐标是字符串", "position": {"top": "10", "left": "20"}},
                    {"words": None},
                    {},
                ],
            },
            {"page_no": 1, "text": "not-a-list"},
        ]
    }

    lines = financial_report_to_markdown(payload).splitlines()

    assert "有效文字" in lines
    assert "坐标非法" in lines
    assert "坐标是字符串" in lines


def test_financial_report_adapter_table_multi_skips_bad_cells():
    payload = {
        "results": [
            {"tables": []},
            {"tables": [{"cells": []}]},
            "not-a-page",
            {
                "tables": [
                    {
                        "cells": [
                            {"row_index": -1, "col_index": 0, "words": "x"},
                            "junk",
                            {"row_index": 0},
                            {"row_index": 0, "col_index": 0, "words": ""},
                        ]
                    }
                ]
            },
            {"tables": "nope"},
        ]
    }

    # 空表 / 全空单元格 / 畸形 cell / 非 dict 节点一律不产出内容
    assert financial_report_to_markdown(payload) == ""
    assert financial_report_to_markdown({"results": "nope"}) == ""
    assert financial_report_to_markdown({}) == ""


def test_financial_report_adapter_prefers_legacy_shape():
    """旧形状优先：旧响应上再塞顶层 results，不允许被劫持到新渲染器。"""
    payload = _sample_payload()
    payload["results"] = [
        {"page_no": 0, "tables": [{"cells": [{"row_index": 0, "col_index": 0, "words": "顶层表格"}]}]}
    ]

    markdown = financial_report_to_markdown(payload)

    assert "## 资产负债表(第1页)" in markdown
    assert "顶层表格" not in markdown


def test_financial_report_loader_validates_table_multi_payload():
    valid = {"code": "00000", "resp_code": "00000", "message": "成功", "results": [{"page_no": 0, "tables": []}]}
    FinancialReportOcrLoader._validate_payload(valid)

    with pytest.raises(KnowledgeFileEmptyError):
        FinancialReportOcrLoader._validate_payload({"code": "00000", "results": []})

    with pytest.raises(EtlException):
        FinancialReportOcrLoader._validate_payload({"code": "10001", "message": "余额不足", "results": [{}]})

    # code 正常但 resp_code 异常，确保第二个业务码键真的被检查到
    with pytest.raises(EtlException):
        FinancialReportOcrLoader._validate_payload({"code": "00000", "resp_code": "10001", "results": [{}]})


# --- bbox 分区：对齐、排序、坐标系 ---


class _FakePdf:
    """`_to_pdf_points` 只需要 len() / 下标 / rect / close()。"""

    def __init__(self, pages: list) -> None:
        self._pages = pages

    def __len__(self) -> int:
        return len(self._pages)

    def __getitem__(self, index: int):
        return self._pages[index]

    def close(self) -> None:
        pass


def _fake_pdf(page_count: int = 1, width: float = 595.0, height: float = 842.0) -> _FakePdf:
    page = SimpleNamespace(rect=SimpleNamespace(width=width, height=height))
    return _FakePdf([page] * page_count)


def _loader(
    tmp_path, *, file_extension: str = "pdf", payload: dict | None = None
) -> tuple[FinancialReportOcrLoader, list[Document]]:
    """跑一次 load()，返回 (loader, documents)，便于断言 metadata 与 bbox_list。"""
    file_path = tmp_path / f"report.{file_extension}"
    file_path.write_bytes(b"stub")
    with patch(
        "bisheng.knowledge.rag.pipeline.loader.financial_report_ocr.requests.post",
        return_value=_mock_response(payload or _sample_table_multi_payload()),
    ):
        loader = FinancialReportOcrLoader(
            file_path=str(file_path),
            file_metadata={"document_id": 1, "knowledge_id": 2},
            file_extension=file_extension,
            tmp_dir=str(tmp_path),
            url="http://ocr.example/ocr/v1/table_multi",
        )
        documents = loader.load()
    return loader, documents


def test_partitions_align_with_markdown():
    """不变量：每个分区的字符区间必须能原样切出它的文本。

    只断言「切片 == text」是**循环的** —— text 就是用同一组偏移切出来的，
    偏移整体错位时两边一起错、断言照样通过（实测过：把行内偏移整体 +1 仍是 0 失败）。
    所以必须再钉一组与偏移无关的期望文本。
    """
    markdown, partitions = build_markdown(_sample_table_multi_payload())

    assert len(partitions) > 0
    # 与偏移无关的期望值：这几段必须真的出自对应的页面文字 / 表格格子
    assert {"不应出现的正文", "物业服务费", "4565.6"} <= {part.text for part in partitions}
    # 页面级文字与表格单元格两种来源都要覆盖到
    assert {part.type for part in partitions} == {"text", "table"}
    for part in partitions:
        assert markdown[part.start : part.end + 1] == part.text


def test_partition_indexes_sorted_and_disjoint():
    """IntervalSearch 对摊平后的区间数组做 bisect，不校验有序性 —— 必须自己保证。"""
    _, partitions = build_markdown(_sample_table_multi_payload())

    assert len(partitions) > 1
    for previous, current in pairwise(partitions):
        assert previous.end < current.start


def test_partition_page_is_one_based():
    """OCR 的 page_no 是 0 基，产出必须是 1 基（前端按 (page - 1) 定位）。"""
    _, partitions = build_markdown(_sample_table_multi_payload())

    assert {part.page for part in partitions} == {1}


def test_partition_bbox_is_int():
    """MinIO 的 key 与前端反查用的 id 都按整数拼，浮点会对不上。"""
    _, partitions = build_markdown(_sample_table_multi_payload())

    for part in partitions:
        assert len(part.bbox) == 4
        assert all(isinstance(value, int) for value in part.bbox)


def test_load_populates_metadata_and_bbox_list(tmp_path):
    """metadata 的四个并行列表必须与 bbox_list 同序同长，且区间能切回各自的文本。"""
    with patch("fitz.open", return_value=_fake_pdf()):
        loader, documents = _loader(tmp_path)

    assert loader.bbox_list
    metadata = documents[0].metadata
    markdown = documents[0].page_content
    for key in ("indexes", "bboxes", "pages", "types"):
        assert len(metadata[key]) == len(loader.bbox_list)

    aligned = zip(loader.bbox_list, metadata["indexes"], metadata["bboxes"], metadata["pages"], strict=True)
    for part, (start, end), bbox, page in aligned:
        assert markdown[start : end + 1] == part.text
        assert part.bbox == bbox
        assert part.page == page == 1


def test_pdf_normalized_to_points_image_not(tmp_path):
    """PDF 必须换算到 PDF point；图片保持自身像素空间（对齐 PaddleOcrLoader 的先例）。"""
    payload = _sample_table_multi_payload()
    # 页 1654×2339 栅格 = 595×842 point（A4），缩放系数 ≈ 0.36
    with patch("fitz.open", return_value=_fake_pdf(width=595.0, height=842.0)):
        pdf_loader, _ = _loader(tmp_path, file_extension="pdf", payload=payload)
    image_loader, _ = _loader(tmp_path, file_extension="png", payload=payload)

    pdf_x2 = max(part.bbox[2] for part in pdf_loader.bbox_list)
    image_x2 = max(part.bbox[2] for part in image_loader.bbox_list)

    assert pdf_x2 < 595  # 换算后落在 A4 宽度内
    assert image_x2 == 1328  # 图片不做换算，原样保留栅格像素


def test_bbox_dropped_when_normalization_fails(tmp_path):
    """换算不了就整体放弃 bbox（降级为无高亮），而不是留下位置错的框。"""
    with patch("fitz.open", side_effect=RuntimeError("boom")):
        loader, documents = _loader(tmp_path)

    assert loader.bbox_list == []
    assert "indexes" not in documents[0].metadata


def test_legacy_shape_has_no_partitions():
    """旧形状没有几何信息，只返回文本。"""
    markdown, partitions = build_markdown(_sample_payload())

    assert "## 资产负债表(第1页)" in markdown
    assert partitions == []
