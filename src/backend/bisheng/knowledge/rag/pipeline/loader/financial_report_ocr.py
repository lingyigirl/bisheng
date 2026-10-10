"""外部财务报表 OCR Loader。"""

import base64
from dataclasses import replace
from typing import Any

import requests
from langchain_core.documents import Document
from loguru import logger

from bisheng.common.errcode.knowledge import KnowledgeFileEmptyError
from bisheng.knowledge.rag.pipeline.loader.base import BaseBishengLoader
from bisheng.knowledge.rag.pipeline.loader.utils.financial_report_adapter import (
    BBoxPartition,
    build_markdown,
    ocr_page_number,
)
from bisheng.knowledge.rag.pipeline.types import TextBbox
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

        data = payload.get("data")
        if data is None:
            # 新版 /ocr/v1/table_multi：业务码与结果直接位于顶层，没有 data 包装。
            # 判据必须是 ``is None`` 而不是「不是 dict」—— 旧服务的业务错误响应形如
            # {"data": {"code": "10001", "message": "..."}}，是个 dict 但没有 results，
            # 用「不是 dict」判会把服务端原始报错吞成 KnowledgeFileEmptyError。
            for key in ("code", "resp_code"):
                if payload.get(key) not in (None, "00000"):
                    raise EtlException(
                        f"financial report OCR failed: code={payload.get(key)}, msg={payload.get('message')}"
                    )
            if not isinstance(payload.get("results"), list) or not payload.get("results"):
                raise KnowledgeFileEmptyError()
            return

        if not isinstance(data, dict):
            raise EtlException("financial report OCR response missing data object")
        if data.get("code") not in (None, "00000"):
            raise EtlException(f"financial report OCR failed: code={data.get('code')}, msg={data.get('message')}")
        if not isinstance(data.get("results"), list) or not data.get("results"):
            raise KnowledgeFileEmptyError()

    def load(self) -> list[Document]:
        payload = self._call_api()
        self._validate_payload(payload)

        markdown, partitions = build_markdown(payload)
        if not markdown.strip():
            raise KnowledgeFileEmptyError()
        partitions = self._to_pdf_points(partitions, payload)

        metadata = self.file_metadata.copy()
        if partitions:
            # 这四个 key 供 ElemCharacterTextSplitter 推算 chunk_bboxes，会被它 pop 掉。
            # 必须与 indexes 同序 —— IntervalSearch 拿摊平后的数组做 bisect，
            # 乱序或重叠不会报错，只会静默算错 chunk 的坐标。
            metadata.update(
                indexes=[[part.start, part.end] for part in partitions],
                bboxes=[part.bbox for part in partitions],
                pages=[part.page for part in partitions],
                types=[part.type for part in partitions],
            )
            # partitions/{file_id}.json 的原料，供前端按 page+bbox 反查对应文本
            self.bbox_list = [
                TextBbox(text=part.text, type=part.type, part_id=str(index), bbox=part.bbox, page=part.page)
                for index, part in enumerate(partitions)
            ]
        return [Document(page_content=markdown, metadata=metadata)]

    def _to_pdf_points(self, partitions: list[BBoxPartition], payload: dict[str, Any]) -> list[BBoxPartition]:
        """把分区的栅格像素坐标换算到 PDF point；换算不了就整体放弃 bbox。

        前端用 pdf.js 的 ``getViewport({scale: 1})``（PDF point 空间，A4 ≈ 595×842）
        画覆盖层，而 OCR 返回的是栅格像素（样例页 1654×2339，约 2.8 倍）—— 不换算的话
        每个框都会画到右下方约 2.8 倍的位置。做法对齐
        ``PaddleOcrLoader._normalize_bboxes_to_pdf_points``。

        图片输入不换算：它的 bbox 空间本来就是图片自身的像素空间。
        换算失败时返回空列表 —— 宁可不画高亮，也不画一个位置错的框。
        """
        if not partitions or self.file_extension != "pdf":
            return partitions
        try:
            import fitz  # PyMuPDF，paddle_ocr / mineru 已在用
        except ImportError:
            logger.warning("financial report OCR bbox dropped: PyMuPDF (fitz) not available")
            return []
        try:
            pdf_doc = fitz.open(self.file_path)
        except Exception:
            logger.warning("financial report OCR bbox dropped: cannot open PDF {}", self.file_path)
            return []
        try:
            scales = self._page_scales(pdf_doc, payload)
        finally:
            pdf_doc.close()

        if not scales:
            logger.warning("financial report OCR bbox dropped: no usable page size for {}", self.file_path)
            return []
        scaled: list[BBoxPartition] = []
        for part in partitions:
            scale = scales.get(part.page)
            if scale is None:
                continue
            sx, sy = scale
            x1, y1, x2, y2 = part.bbox
            # 缩放后必须再截回整数：MinIO 的 key 与前端反查用的 id 都按整数拼
            scaled.append(replace(part, bbox=[int(x1 * sx), int(y1 * sy), int(x2 * sx), int(y2 * sy)]))
        return scaled

    @staticmethod
    def _page_scales(pdf_doc: Any, payload: dict[str, Any]) -> dict[int, tuple[float, float]]:
        """每页的「栅格像素 → PDF point」缩放系数，键是 1 基页码。

        OCR 每页自带栅格尺寸，PDF 页尺寸从 MediaBox 取。OCR 页 → PDF 页优先用响应里的
        ``page_no``（0 基），越界时回落到数组下标 —— 两者只有在 OCR 跳过空白页时才会不一致。
        """
        results = payload.get("results")
        if not isinstance(results, list):
            return {}
        scales: dict[int, tuple[float, float]] = {}
        for index, result in enumerate(results):
            if not isinstance(result, dict):
                continue
            page_no = result.get("page_no")
            pdf_index = page_no if isinstance(page_no, int) and 0 <= page_no < len(pdf_doc) else index
            if not 0 <= pdf_index < len(pdf_doc):
                continue
            try:
                width = float(result.get("width"))
                height = float(result.get("height"))
            except (TypeError, ValueError):
                continue
            if width <= 0 or height <= 0:
                continue
            rect = pdf_doc[pdf_index].rect
            scales[ocr_page_number(result)] = (rect.width / width, rect.height / height)
        return scales
