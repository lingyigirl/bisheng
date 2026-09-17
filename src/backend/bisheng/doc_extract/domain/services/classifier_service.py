"""Decide which extraction template a document belongs to (spec M-2b).

The candidate set is the tenant's *enabled* templates, and the model picks one
of them. Two consequences worth stating plainly:

- The model can never introduce a type. A doc_type that was not offered is a
  failure, not a discovery -- otherwise a hallucinated type would be persisted
  onto an extraction record that no template can ever explain.

- Below the threshold we fail rather than take the best guess. A wrong template
  produces confidently-wrong fields, which is worse than an explicit "could not
  determine" the caller can act on.

This is a separate model call from extraction on purpose: classification wants
"pick one of N + a confidence", extraction wants "fill this one schema", and
combining them makes both outputs fight (design decision 3).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from loguru import logger

from bisheng.common.constants.enums.telemetry import ApplicationTypeEnum
from bisheng.common.errcode.doc_extract import (
    DocExtractContentFetchError,
    DocExtractTypeUnknownError,
)
from bisheng.doc_extract.domain.models.extract_template import (
    DocExtractTemplate,
    DocExtractTemplateDao,
)
from bisheng.doc_extract.domain.services.prompt_builder import build_classify_prompt
from bisheng.llm.domain import LLMService

#: Below this, "could not determine" is the honest answer. Conservative on
#: first release; the spec records that the threshold is global (not per
#: template) as a known limitation.
CONFIDENCE_THRESHOLD = 0.6

# Models often wrap JSON in a fenced block even when told not to. Accepting the
# fence is cheap; rejecting it would burn the whole classification.
_JSON_FENCE_RE = re.compile(r'```(?:json)?\s*(.*?)\s*```', re.DOTALL)


@dataclass
class ClassifyResult:
    doc_type: str
    confidence: float
    template: DocExtractTemplate


class ClassifierService:
    @staticmethod
    def _parse_reply(reply: str) -> tuple[str, float]:
        """Extract ``(doc_type, confidence)`` from the model's reply.

        Raises ``DocExtractContentFetchError`` (11608) when the reply cannot be
        read -- from the caller's point of view an unusable reply and a failed
        call are the same outcome: this run produced nothing.
        """
        text = (reply or '').strip()
        fence = _JSON_FENCE_RE.search(text)
        if fence:
            text = fence.group(1)

        try:
            payload = json.loads(text)
        except (ValueError, TypeError) as exc:
            logger.warning(
                'doc_extract classify reply is not valid JSON: len=%d', len(text)
            )
            raise DocExtractContentFetchError(msg='判定结果不是合法 JSON') from exc

        if not isinstance(payload, dict):
            raise DocExtractContentFetchError(msg='判定结果不是 JSON 对象')

        doc_type = payload.get('doc_type')
        confidence = payload.get('confidence')
        if not isinstance(doc_type, str) or not doc_type.strip():
            raise DocExtractContentFetchError(msg='判定结果缺少 doc_type')
        if isinstance(confidence, bool) or not isinstance(confidence, (int, float)):
            raise DocExtractContentFetchError(msg='判定结果缺少 confidence')
        if not 0 <= float(confidence) <= 1:
            raise DocExtractContentFetchError(msg=f'confidence 超出 0~1 范围：{confidence}')

        return doc_type.strip(), float(confidence)

    @staticmethod
    async def _invoke(llm: Any, prompt: str, document_text: str) -> str:
        """Call the model and return its raw text.

        Kept as its own method so tests can stub it without touching LLM wiring.
        """
        response = await llm.ainvoke([
            {'role': 'system', 'content': prompt},
            {'role': 'user', 'content': document_text},
        ])
        return getattr(response, 'content', '') or ''

    @classmethod
    async def aclassify(
        cls,
        document_text: str,
        *,
        tenant_id: int,
        user_id: int,
        model_id: int | None = None,
    ) -> ClassifyResult:
        """Pick the best-matching enabled template for ``document_text``."""
        templates = await DocExtractTemplateDao.alist_templates(enabled_only=True)
        if not templates:
            # Nothing to classify against. Not a model problem -- there is no
            # candidate set, so the caller must configure a template first.
            raise DocExtractTypeUnknownError(msg='当前租户没有已启用的抽取模板，无法进行类型判定')

        candidates = {t.doc_type: t for t in templates}
        prompt = build_classify_prompt([
            {'doc_type': t.doc_type, 'name': t.name, 'description': t.description}
            for t in templates
        ])

        try:
            llm = LLMService.get_bisheng_llm_sync(
                model_id=model_id,
                app_id=ApplicationTypeEnum.DOC_EXTRACT.value,
                app_name=ApplicationTypeEnum.DOC_EXTRACT.value,
                app_type=ApplicationTypeEnum.DOC_EXTRACT,
                user_id=user_id,
            )
            reply = await cls._invoke(llm, prompt, document_text)
        except DocExtractContentFetchError:
            raise
        except Exception as exc:
            logger.warning('doc_extract classify call failed', exc_info=True)
            logger.warning('doc_extract classify call failed', exc_info=True)
            raise DocExtractContentFetchError(msg='判定模型调用失败') from exc

        doc_type, confidence = cls._parse_reply(reply)

        template = candidates.get(doc_type)
        if template is None:
            # The model named something we never offered. Treat as "could not
            # determine" rather than accepting an unknown type.
            logger.warning(
                'doc_extract classify returned unknown doc_type=%s (offered=%s)',
                doc_type, list(candidates),
            )
            raise DocExtractTypeUnknownError(msg=f'判定结果不在候选类型内：{doc_type}')

        if confidence < CONFIDENCE_THRESHOLD:
            raise DocExtractTypeUnknownError(
                msg=f'判定置信度 {confidence} 低于阈值 {CONFIDENCE_THRESHOLD}，无法确定文档类型'
            )

        return ClassifyResult(doc_type=doc_type, confidence=confidence, template=template)
