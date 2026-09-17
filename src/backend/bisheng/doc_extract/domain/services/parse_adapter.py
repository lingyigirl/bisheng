"""The one place this module touches the knowledge loader stack.

Isolated deliberately: parsing is the part most likely to change (OCR provider
selection, a future vision path), and concentrating it here means those changes
do not ripple through the extraction services.

Two rules this file must keep:

1. It only *calls* ``TempFilePipeline``; it never modifies it. That pipeline
   already serves four call sites (linsight workbench x2, workflow input node,
   workstation chat), so a change here would silently affect all of them.

2. It does not re-implement loader selection. ``_build_pdf_loader`` in
   ``knowledge/rag/base_file_pipeline.py`` is the single source of truth for
   which loader runs; duplicating that decision here is how the two drift apart.
"""

from __future__ import annotations

import os
from typing import Any

from loguru import logger

from bisheng.api.v1.schemas import FileProcessBase
from bisheng.common.errcode.doc_extract import (
    DocExtractContentFetchError,
    DocExtractNoParserError,
)
from bisheng.common.services.config_service import settings
from bisheng.knowledge.rag.temp_file_pipeline import TempFilePipeline

#: Floor for "the parse produced something usable". Deliberately small: a
#: certificate's fields can be short, and an OCR'd image legitimately yields
#: little text. This only exists to catch the empty/near-empty result that a
#: scanned PDF with no text layer produces -- it is NOT a quality bar.
#:
#: An earlier value of 100 rejected a perfectly good OCR result (a 91-character
#: license image) and reported it as "document has no content". Raising this
#: number does not improve extraction; it only invents failures.
MIN_USABLE_TEXT_LENGTH = 10

ROUTE_TEXT = 'text'
ROUTE_OCR = 'ocr'


class ParseAdapter:
    @staticmethod
    def _has_recognition_service() -> bool:
        """Whether this deployment can parse an image / scanned document.

        Delegates to the knowledge config rather than re-deriving it: that
        property already mirrors `base_file_pipeline.py`'s loader selection, so
        the two cannot disagree about what counts as "configured".
        """
        return bool(settings.get_knowledge().image_parser_enabled)

    @staticmethod
    async def run_sync_doc_extract(
        *,
        file_path: str,
        file_name: str,
        user_id: int,
    ) -> tuple[str, str]:
        """Parse ``file_path`` and report which route produced the content.

        Returns ``(text, route)`` with route in ``{'text', 'ocr'}``.

        The route is derived from which loader the pipeline actually selected,
        not guessed from the file extension: when a recognition provider is
        configured, `_build_pdf_loader` hands images and scanned PDFs to it, and
        the caller deserves to know that happened (it dominates the latency and
        is the first thing to check when output looks wrong).

        Raises ``DocExtractNoParserError`` (11605) when nothing usable came out
        and the deployment has no recognition service to fall back to. That is a
        configuration problem, not a document problem, and the message says
        which -- the operator needs to know what to set up.
        """
        file_rule = FileProcessBase(
            knowledge_id=0,
            separator=['\n\n', '\n'],
            separator_rule=['after', 'after'],
            chunk_size=1000,
            chunk_overlap=0,
        )

        text = ''
        loader_name = ''
        try:
            pipeline = TempFilePipeline(
                invoke_user_id=user_id,
                local_file_path=file_path,
                file_name=file_name,
                file_rule=file_rule,
            )
            result = await pipeline.arun()
            text = ParseAdapter._join_documents(result)
            loader_name = type(getattr(pipeline, 'loader', None)).__name__
            if loader_name in ('NoneType', ''):
                loader_name = ''
        except Exception as exc:
            # The parse itself blew up. Whether that is "we have no way to read
            # this" (11605) or "the configured service failed" (11608) depends on
            # whether a service *was* configured -- and the distinction matters,
            # because the two point the operator at different things. Swallowing
            # the exception and reporting 11605 unconditionally (as this did)
            # told a fully-configured deployment to go configure itself.
            logger.warning(
                'doc_extract parse failed file_name=%s', file_name, exc_info=True
            )
            if ParseAdapter._has_recognition_service():
                raise DocExtractContentFetchError(
                    msg='识别服务调用失败或超时，未能解析该文档。'
                ) from exc
            raise DocExtractNoParserError(
                msg=(
                    '文档无可用文字层，且当前环境未配置识别服务。'
                    '请配置 knowledge.loader_provider 对应的识别服务后重试。'
                )
            ) from exc

        if len(text.strip()) >= MIN_USABLE_TEXT_LENGTH:
            return text, ParseAdapter._route_for(loader_name)

        if not ParseAdapter._has_recognition_service():
            raise DocExtractNoParserError(
                msg=(
                    '文档无可用文字层，且当前环境未配置识别服务。'
                    '请配置 knowledge.loader_provider 对应的识别服务后重试。'
                )
            )

        # A recognition service is configured and ran, yet produced nothing
        # usable -- spec §3.1 row 9 (the service failed to yield content), not
        # row 4 (no service exists). Reporting 11605 here would send the
        # operator to configure something that is already configured.
        raise DocExtractContentFetchError(
            msg='已配置识别服务，但未能从该文档中解析出可用内容。'
        )

    #: Loaders that mean "an external recognition service did the reading".
    _OCR_LOADER_NAMES = ('MineruLoader', 'PaddleOcrLoader', 'Etl4lmLoader')

    @staticmethod
    def _route_for(loader_name: str) -> str:
        """Map the loader the pipeline picked onto the route the caller sees."""
        if loader_name in ParseAdapter._OCR_LOADER_NAMES:
            return ROUTE_OCR
        return ROUTE_TEXT

    @staticmethod
    def _join_documents(result: Any) -> str:
        """Concatenate parsed pages, dropping whitespace-only ones.

        Blank pages otherwise inflate the length check that decides the route,
        and they add nothing to the model's input.
        """
        parts = [
            (doc.page_content or '').strip()
            for doc in (getattr(result, 'documents', None) or [])
        ]
        return '\n\n'.join(p for p in parts if p)


def cleanup_temp_file(file_path: str) -> None:
    """Best-effort removal of a staged upload."""
    if file_path and os.path.exists(file_path):
        try:
            os.remove(file_path)
        except OSError:
            logger.warning('doc_extract temp cleanup failed path=%s', file_path, exc_info=True)
