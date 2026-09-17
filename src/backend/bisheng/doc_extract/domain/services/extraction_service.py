"""Extraction orchestration: prechecks, classify, extract, render, record.

The precheck table (spec §3.1) is ordered and each row has a fixed code. Two
properties matter and are easy to lose in a refactor:

1. **First match wins.** A request can violate several conditions at once; the
   spec fixes which code comes back so the answer does not depend on the order
   someone happened to write the checks in.

2. **The stage boundary decides whether a record is written.** Rows 1-8 are
   "rejected before extraction" -- there is no template to attribute a row to,
   so nothing is recorded. Rows 9-11 are "failed during extraction", where the
   template is known and the failure is worth remembering.

Trial runs (spec M-1b) skip the business prechecks entirely: deciding whether a
template *should* be used is the question the trial exists to answer. They keep
only the hard preconditions (readable, not oversize, model configured, content
obtainable) and never write a record.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from typing import Any

from loguru import logger

from bisheng.common.constants.enums.telemetry import ApplicationTypeEnum
from bisheng.common.errcode.doc_extract import (
    DocExtractContentFetchError,
    DocExtractDocTooLargeError,
    DocExtractDocUnreadableError,
    DocExtractFieldInvalidError,
    DocExtractTemplateMissingError,
    DocExtractTypeUnavailableError,
)
from bisheng.doc_extract.domain.models.extract_run import DocExtractRunDao
from bisheng.doc_extract.domain.models.extract_template import (
    DocExtractTemplateDao,
)
from bisheng.doc_extract.domain.services.classifier_service import ClassifierService
from bisheng.doc_extract.domain.services.field_validator import validate_fields
from bisheng.doc_extract.domain.services.parse_adapter import ParseAdapter
from bisheng.doc_extract.domain.services.prompt_builder import (
    build_extract_messages,
    build_retry_messages,
)
from bisheng.doc_extract.domain.services.renderer import render
from bisheng.doc_extract.domain.services.template_service import TemplateService
from bisheng.llm.domain import LLMService

#: 20 MB. Certificate photos and scans run 1-5 MB; this leaves room for a
#: high-resolution scan without letting one request monopolise the parser.
MAX_DOCUMENT_BYTES = 20 * 1024 * 1024

#: One initial attempt plus two retries. Most format failures clear on the
#: first retry; beyond that the cost outweighs the shrinking chance of success.
MAX_RETRIES = 2

_JSON_FENCE_RE = re.compile(r'```(?:json)?\s*(.*?)\s*```', re.DOTALL)

DETERMINED_BY_EXPLICIT = 'explicit'
DETERMINED_BY_AUTO = 'auto'

STATUS_SUCCESS = 1
STATUS_FAILED = 0


@dataclass
class ExtractResult:
    ok: bool
    fields: dict[str, Any] = field(default_factory=dict)
    markdown: str = ''
    template: dict[str, Any] = field(default_factory=dict)
    confidence: float | None = None
    route: str | None = None
    determined_by: str = DETERMINED_BY_EXPLICIT
    missing_fields: list[str] = field(default_factory=list)
    error_code: int | None = None
    error_message: str | None = None
    reason: str | None = None


class ExtractionService:
    # --- model slot ---------------------------------------------------------

    @staticmethod
    def _resolve_model_id(tenant_id: int | None) -> int | None:
        """Read the tenant's configured extraction model.

        Returns None when unconfigured, which the caller turns into 11408 before
        any parsing work happens -- no point parsing a document we cannot
        extract from.
        """
        from bisheng.common.models.config import ConfigKeyEnum
        from bisheng.llm.domain.models.tenant_system_model_config import (
            TenantSystemModelConfigDao,
        )

        try:
            value, _inherited, _blocked = TenantSystemModelConfigDao.resolve(
                ConfigKeyEnum.DOC_EXTRACT_LLM.value, tenant_id
            )
        except Exception:
            logger.warning('doc_extract model slot resolve failed', exc_info=True)
            return None

        if not value:
            return None
        if isinstance(value, dict):
            model_id = value.get('model_id') or value.get('extract_model_id')
            return int(model_id) if model_id else None
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    # --- model calls --------------------------------------------------------

    @staticmethod
    async def _invoke(llm: Any, messages: list[dict[str, str]]) -> str:
        response = await llm.ainvoke(messages)
        return getattr(response, 'content', '') or ''

    @staticmethod
    def _parse_fields(reply: str) -> dict[str, Any]:
        """Read the model's JSON reply.

        Malformed JSON is a *format* problem, so it is worth retrying -- unlike
        a transport failure, which is not (see the caller).
        """
        text = (reply or '').strip()
        fence = _JSON_FENCE_RE.search(text)
        if fence:
            text = fence.group(1)
        try:
            payload = json.loads(text)
        except (ValueError, TypeError) as exc:
            raise DocExtractFieldInvalidError(
                msg=f'模型输出不是合法 JSON：{text[:200]}'
            ) from exc
        if not isinstance(payload, dict):
            raise DocExtractFieldInvalidError(msg='模型输出不是 JSON 对象')
        return payload

    # --- prechecks ----------------------------------------------------------

    @staticmethod
    def _assert_readable(file_path: str) -> int:
        """Row 1. Returns the byte size for the row-2 check."""
        if not file_path or not os.path.isfile(file_path):
            raise DocExtractDocUnreadableError(msg='文档不存在或无法读取')
        size = os.path.getsize(file_path)
        if size == 0:
            raise DocExtractDocUnreadableError(msg='文档为空')
        return size

    @staticmethod
    def _assert_within_size(size: int) -> None:
        """Row 2."""
        if size > MAX_DOCUMENT_BYTES:
            raise DocExtractDocTooLargeError(
                msg=f'文档超出体量上限（{MAX_DOCUMENT_BYTES // 1024 // 1024} MB）'
            )

    @classmethod
    def _resolve_template_payload(
        cls,
        *,
        dry_run: bool,
        template_id: int | None,
        trial_definition: dict[str, Any] | None,
        saved_template: Any,
    ) -> dict[str, Any]:
        """Build the definition this run executes against.

        For a trial run that is the editor's unsaved definition (AC-41); it was
        already validated by the caller before parsing (AC-42).
        """
        if not dry_run:
            return {
                'id': saved_template.id,
                'name': saved_template.name,
                'doc_type': saved_template.doc_type,
                'field_schema': saved_template.field_schema or [],
                'render_template': saved_template.render_template or '',
                'tenant_id': getattr(saved_template, 'tenant_id', None),
            }

        definition = trial_definition or {}
        return {
            'id': template_id,
            'name': getattr(saved_template, 'name', ''),
            'doc_type': getattr(saved_template, 'doc_type', ''),
            'field_schema': definition.get('field_schema') or [],
            'render_template': definition.get('render_template') or '',
            'tenant_id': getattr(saved_template, 'tenant_id', None),
        }

    @staticmethod
    def _validate_trial_definition(field_schema: list[dict], render_template: str) -> None:
        TemplateService.validate_definition(
            name='trial',
            doc_type='trial',
            description='trial',
            field_schema=field_schema,
            render_template=render_template,
        )

    # --- main entry ---------------------------------------------------------

    @classmethod
    async def extract(
        cls,
        *,
        file_path: str,
        file_name: str,
        user_id: int,
        doc_type: str | None = None,
        dry_run: bool = False,
        template_id: int | None = None,
        trial_definition: dict[str, Any] | None = None,
        tenant_id: int | None = None,
    ) -> ExtractResult:
        # --- rows 1-3: apply to every mode, including trial ---------------
        size = cls._assert_readable(file_path)
        cls._assert_within_size(size)

        model_id = cls._resolve_model_id(tenant_id)
        if model_id is None:
            raise DocExtractContentFetchError(
                msg='当前租户未配置文档抽取模型（doc_extract_llm），请先配置'
            )

        # --- resolve the template ------------------------------------------
        confidence: float | None = None
        determined_by = DETERMINED_BY_EXPLICIT
        saved_template = None

        if dry_run:
            if template_id is None:
                raise DocExtractTemplateMissingError(msg='试抽取需要指定模板')
            saved_template = await DocExtractTemplateDao.aget_by_id(template_id)
            if saved_template is None:
                raise DocExtractTemplateMissingError()
            # AC-42: reject an unrunnable editor definition *before* parsing --
            # doing the expensive work first and then rejecting wastes a full
            # document parse on a request that could never have succeeded.
            definition = trial_definition or {}
            cls._validate_trial_definition(
                definition.get('field_schema') or [],
                definition.get('render_template') or '',
            )
        elif doc_type:
            saved_template = await DocExtractTemplateDao.aget_by_doc_type(doc_type)
            # Row 5: unknown and disabled are one answer to the caller.
            if saved_template is None or not saved_template.enabled:
                raise DocExtractTypeUnavailableError()
        # else: row 6/7/8 (auto-classify) are handled after parsing, because
        # classification needs the document content.

        # --- row 4/6: content -----------------------------------------------
        text, route = await ParseAdapter.run_sync_doc_extract(
            file_path=file_path, file_name=file_name, user_id=user_id
        )

        # --- rows 6-8: auto classification ----------------------------------
        if not dry_run and not doc_type:
            classify = await ClassifierService.aclassify(
                text, tenant_id=tenant_id, user_id=user_id, model_id=model_id
            )
            saved_template = classify.template
            confidence = classify.confidence
            determined_by = DETERMINED_BY_AUTO

        payload = cls._resolve_template_payload(
            dry_run=dry_run,
            template_id=template_id,
            trial_definition=trial_definition,
            saved_template=saved_template,
        )
        field_schema = payload['field_schema']
        render_template = payload['render_template']

        # --- rows 9-11: extraction, with records ----------------------------
        try:
            result = await cls._run_extraction(
                text=text,
                route=route,
                payload=payload,
                model_id=model_id,
                user_id=user_id,
                confidence=confidence,
                determined_by=determined_by,
            )
        except Exception as exc:
            if not dry_run:
                await cls._record_failure(
                    payload=payload,
                    route=route,
                    user_id=user_id,
                    confidence=confidence,
                    determined_by=determined_by,
                    exc=exc,
                )
            raise

        if not dry_run:
            await cls._record_success(
                payload=payload,
                route=route,
                user_id=user_id,
                confidence=confidence,
                determined_by=determined_by,
                result=result,
                field_schema=field_schema,
            )

        result.template = {
            'id': payload['id'],
            'doc_type': payload['doc_type'],
            'name': payload['name'],
            'field_schema': field_schema,
        }
        return result

    # --- extraction loop ----------------------------------------------------

    @classmethod
    async def _run_extraction(
        cls,
        *,
        text: str,
        route: str,
        payload: dict[str, Any],
        model_id: int,
        user_id: int,
        confidence: float | None,
        determined_by: str,
    ) -> ExtractResult:
        field_schema = payload['field_schema']
        render_template = payload['render_template']

        try:
            llm = LLMService.get_bisheng_llm_sync(
                model_id=model_id,
                app_id=ApplicationTypeEnum.DOC_EXTRACT.value,
                app_name=ApplicationTypeEnum.DOC_EXTRACT.value,
                app_type=ApplicationTypeEnum.DOC_EXTRACT,
                user_id=user_id,
            )
        except Exception as exc:
            raise DocExtractContentFetchError(msg=f'抽取模型初始化失败：{exc}') from exc

        messages = build_extract_messages(text, field_schema)
        last_errors: list[dict[str, Any]] = []
        reply = ''

        for attempt in range(MAX_RETRIES + 1):
            try:
                reply = await cls._invoke(llm, messages)
            except Exception as exc:
                # Transport failure: retrying multiplies the delay without
                # improving the odds (spec AC-24 / row 10).
                raise DocExtractContentFetchError(msg=f'抽取模型调用失败：{exc}') from exc

            try:
                fields = cls._parse_fields(reply)
            except DocExtractFieldInvalidError as exc:
                # Format problem -- worth another attempt with feedback.
                last_errors = [{'field_key': '_', 'label': '_', 'expected': 'JSON',
                                'actual': reply[:200], 'reason': 'malformed_json'}]
                if attempt < MAX_RETRIES:
                    messages = build_retry_messages(messages, reply, last_errors)
                    continue
                raise

            ok, errors = validate_fields(fields, field_schema)
            if ok:
                kept = {f['key']: fields.get(f['key']) for f in field_schema if f.get('key')}
                missing = [k for k, v in kept.items() if v is None or v == '']
                # Drop keys the schema does not define (spec AC-27).
                return ExtractResult(
                    ok=True,
                    fields={k: v for k, v in kept.items() if v is not None},
                    markdown=render(render_template, kept),
                    confidence=confidence,
                    route=route,
                    determined_by=determined_by,
                    missing_fields=missing,
                )

            last_errors = errors
            if attempt < MAX_RETRIES:
                messages = build_retry_messages(messages, reply, errors)

        raise DocExtractFieldInvalidError(
            msg='抽取结果不符合字段定义：' + '；'.join(
                f"{e.get('field_key')}({e.get('reason')})" for e in last_errors
            )
        )

    # --- records ------------------------------------------------------------

    @staticmethod
    async def _record_success(
        *,
        payload: dict[str, Any],
        route: str,
        user_id: int,
        confidence: float | None,
        determined_by: str,
        result: ExtractResult,
        field_schema: list[dict[str, Any]],
    ) -> None:
        try:
            await DocExtractRunDao.acreate({
                'template_id': payload['id'],
                'doc_type': payload['doc_type'],
                'determined_by': determined_by,
                'confidence': confidence,
                'preprocess_route': route,
                'fields': result.fields,
                'markdown': result.markdown,
                'status': STATUS_SUCCESS,
                'user_id': user_id,
                'tenant_id': payload.get('tenant_id'),
            })
        except Exception:
            # A record-write failure must not fail an extraction that succeeded.
            logger.exception('doc_extract run record write failed')

    @staticmethod
    async def _record_failure(
        *,
        payload: dict[str, Any],
        route: str,
        user_id: int,
        confidence: float | None,
        determined_by: str,
        exc: Exception,
    ) -> None:
        error_code = getattr(exc, 'code', None)
        try:
            await DocExtractRunDao.acreate({
                'template_id': payload['id'],
                'doc_type': payload['doc_type'],
                'determined_by': determined_by,
                'confidence': confidence,
                'preprocess_route': route,
                'fields': {},
                'markdown': '',
                'status': STATUS_FAILED,
                'error_code': error_code,
                'error_message': str(exc)[:500],
                'user_id': user_id,
                'tenant_id': payload.get('tenant_id'),
            })
        except Exception:
            logger.exception('doc_extract failure record write failed')

    # --- trial-run permission gate -----------------------------------------

    @staticmethod
    async def assert_trial_allowed(user: Any) -> None:
        """Gate trial runs to tenant admins / platform super admins.

        Lives in the service rather than the endpoint's `Depends` because
        FastAPI's dependency chain cannot see the request body, so it cannot
        branch on `dry_run`. Skipping this would let any signed-in user pass
        `dry_run=true` to probe or use templates they have no business touching
        -- a privilege escalation, not a convenience gap.
        """
        from bisheng.common.dependencies.user_deps import UserPayload
        from bisheng.core.context.tenant import get_current_tenant_id
        from bisheng.utils.http_middleware import _check_is_global_super

        if isinstance(user, UserPayload):
            user_id = user.user_id
        else:
            user_id = getattr(user, 'user_id', None)

        if user_id is None:
            raise DocExtractTemplateMissingError(msg='无法识别调用者身份')

        try:
            if await _check_is_global_super(user_id):
                return
        except Exception:
            logger.warning('doc_extract super-admin check failed', exc_info=True)

        tenant_id = get_current_tenant_id()
        has_admin = getattr(user, 'has_tenant_admin', None)
        if tenant_id is not None and callable(has_admin):
            try:
                if await has_admin(tenant_id):
                    return
            except Exception:
                logger.warning('doc_extract tenant-admin check failed', exc_info=True)

        raise DocExtractTemplateMissingError(msg='试抽取仅限租户管理员或平台超级管理员使用')
