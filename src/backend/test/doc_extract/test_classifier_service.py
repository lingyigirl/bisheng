"""Document type classification (spec M-2b).

Covers AC-16 (returns candidates + confidence), AC-17 (only this tenant's
enabled templates are candidates), AC-18 (above threshold -> a type is chosen)
and AC-19 (below threshold -> 11606).

The model is mocked everywhere -- these tests are about the surrounding rules,
not about what a model would answer.
"""

from unittest.mock import AsyncMock, patch

import pytest

from bisheng.common.errcode.doc_extract import (
    DocExtractContentFetchError,
    DocExtractTypeUnknownError,
)
from bisheng.doc_extract.domain.services.classifier_service import (
    CONFIDENCE_THRESHOLD,
    ClassifierService,
)

SERVICE = 'bisheng.doc_extract.domain.services.classifier_service'


def _template(doc_type='business_license', name='营业执照', enabled=1, **kw):
    tpl = type('T', (), {})()
    tpl.id = kw.pop('id', 1)
    tpl.doc_type = doc_type
    tpl.name = name
    tpl.enabled = enabled
    tpl.description = kw.pop('description', '工商行政管理部门颁发的企业登记证件')
    return tpl


@pytest.fixture()
def enabled_template():
    return [_template()]


def _mock_llm(reply: str):
    """Patch the LLM factory + the invoke helper to return ``reply``."""
    llm = object()
    return patch(f'{SERVICE}.LLMService.get_bisheng_llm_sync', return_value=llm), patch(
        f'{SERVICE}.ClassifierService._invoke', new_callable=AsyncMock, return_value=reply
    )


async def test_confident_match_returns_type_and_confidence(enabled_template):
    p1, p2 = _mock_llm('{"doc_type": "business_license", "confidence": 0.94}')
    with (
        patch(f'{SERVICE}.DocExtractTemplateDao.alist_templates', new_callable=AsyncMock, return_value=enabled_template),
        p1, p2,
    ):
        result = await ClassifierService.aclassify('some text', tenant_id=1, user_id=7)

    assert result.doc_type == 'business_license'
    assert result.confidence == pytest.approx(0.94)
    assert result.template is enabled_template[0]


async def test_only_enabled_templates_are_candidates():
    """AC-17: disabled templates must not even be offered to the model."""
    p1, p2 = _mock_llm('{"doc_type": "business_license", "confidence": 0.9}')
    with (
        patch(f'{SERVICE}.DocExtractTemplateDao.alist_templates', new_callable=AsyncMock) as lister,
        p1, p2,
    ):
        lister.return_value = [_template()]
        await ClassifierService.aclassify('text', tenant_id=1, user_id=7)

    # The DAO call must ask for enabled-only rows.
    assert lister.await_args.kwargs.get('enabled_only') is True


async def test_no_enabled_templates_raises_11606():
    with patch(f'{SERVICE}.DocExtractTemplateDao.alist_templates', new_callable=AsyncMock, return_value=[]):
        with pytest.raises(DocExtractTypeUnknownError):
            await ClassifierService.aclassify('text', tenant_id=1, user_id=7)


async def test_confidence_below_threshold_raises_11606(enabled_template):
    """AC-19: a low-confidence guess must not silently become a wrong extraction."""
    low = CONFIDENCE_THRESHOLD - 0.1
    p1, p2 = _mock_llm(f'{{"doc_type": "business_license", "confidence": {low}}}')
    with (
        patch(f'{SERVICE}.DocExtractTemplateDao.alist_templates', new_callable=AsyncMock, return_value=enabled_template),
        p1, p2,
    ):
        with pytest.raises(DocExtractTypeUnknownError):
            await ClassifierService.aclassify('text', tenant_id=1, user_id=7)


async def test_confidence_exactly_at_threshold_is_accepted(enabled_template):
    p1, p2 = _mock_llm(f'{{"doc_type": "business_license", "confidence": {CONFIDENCE_THRESHOLD}}}')
    with (
        patch(f'{SERVICE}.DocExtractTemplateDao.alist_templates', new_callable=AsyncMock, return_value=enabled_template),
        p1, p2,
    ):
        result = await ClassifierService.aclassify('text', tenant_id=1, user_id=7)
    assert result.doc_type == 'business_license'


async def test_unknown_doc_type_from_model_is_rejected(enabled_template):
    """The model must pick from the offered set; anything else is a failure,
    not a new template invented on the fly."""
    p1, p2 = _mock_llm('{"doc_type": "driver_license", "confidence": 0.99}')
    with (
        patch(f'{SERVICE}.DocExtractTemplateDao.alist_templates', new_callable=AsyncMock, return_value=enabled_template),
        p1, p2,
    ):
        with pytest.raises(DocExtractTypeUnknownError):
            await ClassifierService.aclassify('text', tenant_id=1, user_id=7)


async def test_malformed_json_raises_11608(enabled_template):
    p1, p2 = _mock_llm('not json at all')
    with (
        patch(f'{SERVICE}.DocExtractTemplateDao.alist_templates', new_callable=AsyncMock, return_value=enabled_template),
        p1, p2,
    ):
        with pytest.raises(DocExtractContentFetchError):
            await ClassifierService.aclassify('text', tenant_id=1, user_id=7)


async def test_json_without_confidence_raises_11608(enabled_template):
    p1, p2 = _mock_llm('{"doc_type": "business_license"}')
    with (
        patch(f'{SERVICE}.DocExtractTemplateDao.alist_templates', new_callable=AsyncMock, return_value=enabled_template),
        p1, p2,
    ):
        with pytest.raises(DocExtractContentFetchError):
            await ClassifierService.aclassify('text', tenant_id=1, user_id=7)


async def test_json_wrapped_in_markdown_fence_is_parsed(enabled_template):
    """Models routinely fence JSON in ```json blocks despite being told not to."""
    p1, p2 = _mock_llm('```json\n{"doc_type": "business_license", "confidence": 0.9}\n```')
    with (
        patch(f'{SERVICE}.DocExtractTemplateDao.alist_templates', new_callable=AsyncMock, return_value=enabled_template),
        p1, p2,
    ):
        result = await ClassifierService.aclassify('text', tenant_id=1, user_id=7)
    assert result.doc_type == 'business_license'


async def test_model_exception_raises_11608(enabled_template):
    with (
        patch(f'{SERVICE}.DocExtractTemplateDao.alist_templates', new_callable=AsyncMock, return_value=enabled_template),
        patch(f'{SERVICE}.LLMService.get_bisheng_llm_sync', side_effect=RuntimeError('boom')),
    ):
        with pytest.raises(DocExtractContentFetchError):
            await ClassifierService.aclassify('text', tenant_id=1, user_id=7)


async def test_confidence_out_of_range_raises_11608(enabled_template):
    p1, p2 = _mock_llm('{"doc_type": "business_license", "confidence": 1.5}')
    with (
        patch(f'{SERVICE}.DocExtractTemplateDao.alist_templates', new_callable=AsyncMock, return_value=enabled_template),
        p1, p2,
    ):
        with pytest.raises(DocExtractContentFetchError):
            await ClassifierService.aclassify('text', tenant_id=1, user_id=7)
