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
from bisheng.common.errcode.doc_extract import DocExtractNoParserError
from bisheng.common.services.config_service import settings
from bisheng.knowledge.rag.temp_file_pipeline import TempFilePipeline

#: A scanned PDF often carries a thin, useless text layer. Anything below this
#: many characters is treated as "no real content" and sent to recognition
#: instead of being extracted from noise. Low on purpose: a short but genuine
#: document must not be pushed through OCR needlessly.
MIN_USABLE_TEXT_LENGTH = 100

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

        Raises ``DocExtractNoParserError`` (11405) when there is no usable text
        and the deployment has no recognition service to fall back to. That is
        a configuration problem, not a document problem, and the message says
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
        try:
            pipeline = TempFilePipeline(
                invoke_user_id=user_id,
                local_file_path=file_path,
                file_name=file_name,
                file_rule=file_rule,
            )
            result = await pipeline.arun()
            text = ParseAdapter._join_documents(result)
        except Exception:
            # A parse failure is not necessarily fatal -- it is exactly the
            # "scanned document" case, so fall through to the route decision
            # below rather than failing here.
            logger.warning(
                'doc_extract parse produced no content file_name=%s', file_name, exc_info=True
            )
            text = ''

        if len(text.strip()) >= MIN_USABLE_TEXT_LENGTH:
            return text, ROUTE_TEXT

        if not ParseAdapter._has_recognition_service():
            raise DocExtractNoParserError(
                msg=(
                    '文档无可用文字层，且当前环境未配置识别服务。'
                    '请配置 knowledge.loader_provider 对应的识别服务后重试。'
                )
            )

        # A recognition service is configured, but wiring the OCR branch is the
        # second phase of this feature (design §9) -- everything before it runs
        # without any external service.
        raise DocExtractNoParserError(
            msg='识别服务分支尚未启用（第二阶段实现）。'
        )

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
