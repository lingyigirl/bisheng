"""外部财务报表 OCR Loader。"""

import base64
from typing import Any

import requests
from langchain_core.documents import Document

from bisheng.common.errcode.knowledge import KnowledgeFileEmptyError
from bisheng.knowledge.rag.pipeline.loader.base import BaseBishengLoader
from bisheng.knowledge.rag.pipeline.loader.utils.financial_report_adapter import (
    financial_report_to_markdown,
)
from bisheng.utils.exceptions import EtlException


class FinancialReportOcrLoader(BaseBishengLoader):
    """调用外部财务报表 OCR, 并将 JSON 响应转换为 Document。"""

    def __init__(
        self,
        url: str,
        timeout: int = 60,
        headers: dict | None = None,
        request_kwargs: dict | None = None,
        request_mode: str = "multipart",
        file_field: str = "file",
        *args,
        **kwargs,
    ):
        super().__init__(*args, **kwargs)
        self.url = url.rstrip("/")
        self.timeout = timeout
        self.headers = headers or {}
        self.request_kwargs = request_kwargs or {}
        self.request_mode = request_mode
        self.file_field = file_field

    def _post(self, **kwargs) -> dict[str, Any]:
        """调用外部 OCR, 并统一处理网络和响应错误。"""
        try:
            response = requests.post(
                self.url,
                headers=self.headers,
                timeout=self.timeout,
                **kwargs,
            )
        except requests.Timeout as exc:
            raise EtlException("financial report OCR timeout") from exc
        except requests.RequestException as exc:
            raise EtlException(f"financial report OCR request failed: {exc}") from exc

        if response.status_code != 200:
            raise EtlException(f"financial report OCR error: status={response.status_code}, resp={response.text}")

        try:
            payload = response.json()
        except ValueError as exc:
            raise EtlException("financial report OCR returned invalid JSON") from exc

        if not isinstance(payload, dict):
            raise EtlException("financial report OCR response must be a JSON object")
        return payload

    def _call_multipart(self) -> dict[str, Any]:
        """以 multipart/form-data 上传原文件。"""
        with open(self.file_path, "rb") as file_obj:
            files = {
                self.file_field: (
                    self.file_name,
                    file_obj,
                    "application/octet-stream",
                )
            }
            return self._post(files=files, data=self.request_kwargs)

    def _call_base64_json(self) -> dict[str, Any]:
        """以 JSON base64 方式提交原文件。"""
        with open(self.file_path, "rb") as file_obj:
            encoded = base64.b64encode(file_obj.read()).decode("ascii")

        payload = {self.file_field: encoded}
        payload.update(self.request_kwargs)
        return self._post(json=payload)

    def _call_api(self) -> dict[str, Any]:
        """按配置选择请求方式。"""
        if self.request_mode == "multipart":
            return self._call_multipart()
        if self.request_mode == "base64_json":
            return self._call_base64_json()
        raise EtlException(f"unsupported financial report OCR request mode: {self.request_mode}")

    @staticmethod
    def _validate_payload(payload: dict[str, Any]) -> None:
        """校验财务报表 OCR 的业务返回码和结果结构。"""
        error = payload.get("e") or {}
        if isinstance(error, dict) and error.get("code") not in (None, 200):
            raise EtlException(f"financial report OCR failed: code={error.get('code')}, msg={error.get('msg')}")

        data = payload.get("data") or {}
        if not isinstance(data, dict):
            raise EtlException("financial report OCR response missing data object")
        if data.get("code") not in (None, "00000"):
            raise EtlException(f"financial report OCR failed: code={data.get('code')}, msg={data.get('message')}")
        if not isinstance(data.get("results"), list) or not data.get("results"):
            raise KnowledgeFileEmptyError()

    def load(self) -> list[Document]:
        payload = self._call_api()
        self._validate_payload(payload)
        markdown = financial_report_to_markdown(payload)
        if not markdown.strip():
            raise KnowledgeFileEmptyError()
        return [Document(page_content=markdown, metadata=self.file_metadata.copy())]
