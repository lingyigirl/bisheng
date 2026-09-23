import base64
from unittest.mock import Mock, patch

import pytest

from bisheng.api.v1.schemas import FileProcessBase
from bisheng.core.config.settings import FinancialReportOcrConf, KnowledgeConf
from bisheng.knowledge.rag.base_file_pipeline import BaseFilePipeline
from bisheng.knowledge.rag.pipeline.loader.financial_report_ocr import FinancialReportOcrLoader
from bisheng.knowledge.rag.pipeline.loader.utils.financial_report_adapter import (
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
