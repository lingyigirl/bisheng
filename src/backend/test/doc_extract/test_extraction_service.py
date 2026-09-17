"""Extraction orchestration: the precheck table, retry loop, and dry_run gate.

The precheck table (spec §3.1) is ordered and each row has a fixed code. The
tests below cover it row by row, plus the two properties that make the ordering
meaningful: first-match-wins, and the split between "rejected before extraction"
(no record) and "failed during extraction" (recorded).

Model and parser are mocked throughout.
"""

from unittest.mock import AsyncMock, patch

import pytest

from bisheng.common.errcode.doc_extract import (
    DocExtractContentFetchError,
    DocExtractDocTooLargeError,
    DocExtractDocUnreadableError,
    DocExtractFieldInvalidError,
    DocExtractNoParserError,
    DocExtractTypeUnavailableError,
    DocExtractTemplateMissingError,
)
from bisheng.doc_extract.domain.services.extraction_service import (
    MAX_DOCUMENT_BYTES,
    MAX_RETRIES,
    ExtractionService,
)

SERVICE = 'bisheng.doc_extract.domain.services.extraction_service'
SCHEMA = [
    {'key': 'company_name', 'label': '企业名称', 'type': 'string', 'required': True},
]
TEMPLATE = {
    'id': 12,
    'doc_type': 'business_license',
    'name': '营业执照',
    'description': 'desc',
    'field_schema': SCHEMA,
    'render_template': '名称：{{company_name}}',
    'enabled': 1,
    'is_deleted': 0,
    'tenant_id': 1,
}


def _run(**overrides):
    kwargs = {
        'file_path': '/tmp/x.pdf',
        'file_name': 'x.pdf',
        'user_id': 7,
        'dry_run': False,
    }
    kwargs.update(overrides)
    return ExtractionService.extract(**kwargs)


def _patch_ok_parse(text='营业执照内容' * 30):
    return patch(
        f'{SERVICE}.ParseAdapter.run_sync_doc_extract',
        new_callable=AsyncMock,
        return_value=(text, 'text'),
    )


class _ModelPatches:
    """Context manager wiring a fake model: factory, invoke, and the slot.

    ``invoke`` is exposed so tests can assert on the retry count.
    """

    def __init__(self, reply='{"company_name": "广西某某有限公司"}', side_effect=None):
        self._factory = patch(f'{SERVICE}.LLMService.get_bisheng_llm_sync', return_value=object())
        self._slot = patch(f'{SERVICE}.ExtractionService._resolve_model_id', return_value=99)
        if side_effect is not None:
            self.invoke = patch(f'{SERVICE}.ExtractionService._invoke',
                                new_callable=AsyncMock, side_effect=side_effect)
        else:
            self.invoke = patch(f'{SERVICE}.ExtractionService._invoke',
                                new_callable=AsyncMock, return_value=reply)

    def __enter__(self):
        self._factory.start()
        self._slot.start()
        # `.start()` returns the mock itself; keep that, not the _patch handle,
        # so tests can assert on `await_count`.
        self.invoke = self.invoke.start()
        return self

    def __exit__(self, *exc):
        self.invoke.stop()
        self._slot.stop()
        self._factory.stop()
        return False


def _patch_model(reply='{"company_name": "广西某某有限公司"}'):
    return _ModelPatches(reply=reply)


def _patch_model_failing(exc):
    return _ModelPatches(side_effect=exc)


def _patch_template(template=TEMPLATE):
    return patch(
        f'{SERVICE}.DocExtractTemplateDao.aget_by_id',
        new_callable=AsyncMock,
        return_value=type('T', (), template)(),
    )


def _patch_run_dao():
    return patch(f'{SERVICE}.DocExtractRunDao.acreate', new_callable=AsyncMock)


# --- precheck row 1: unreadable / empty ------------------------------------


async def test_missing_file_raises_11409():
    with pytest.raises(DocExtractDocUnreadableError):
        await _run(file_path='/definitely/not/here.pdf')


async def test_empty_file_raises_11409(tmp_path):
    empty = tmp_path / 'empty.pdf'
    empty.write_bytes(b'')
    with pytest.raises(DocExtractDocUnreadableError):
        await _run(file_path=str(empty))


# --- precheck row 2: oversize ----------------------------------------------


async def test_oversize_document_raises_11410(tmp_path):
    big = tmp_path / 'big.pdf'
    big.write_bytes(b'x' * (MAX_DOCUMENT_BYTES + 1))
    with pytest.raises(DocExtractDocTooLargeError):
        await _run(file_path=str(big))


async def test_document_at_limit_is_accepted(tmp_path):
    """Boundary: exactly the limit is allowed."""
    at_limit = tmp_path / 'limit.pdf'
    at_limit.write_bytes(b'x' * MAX_DOCUMENT_BYTES)
    model = _patch_model()
    with (
        _patch_ok_parse(), model,
        patch(f'{SERVICE}.DocExtractTemplateDao.aget_by_doc_type', new_callable=AsyncMock,
              return_value=type('T', (), TEMPLATE)()),
        _patch_run_dao(),
    ):
        result = await _run(file_path=str(at_limit), doc_type='business_license')
    assert result.ok is True


# --- precheck row 3: model unconfigured ------------------------------------


async def test_unconfigured_model_raises_11408(tmp_path):
    doc = tmp_path / 'x.pdf'
    doc.write_bytes(b'x' * 10)
    with patch(f'{SERVICE}.ExtractionService._resolve_model_id', return_value=None):
        with pytest.raises(DocExtractContentFetchError):
            await _run(file_path=str(doc))


# --- precheck row 5: explicit type unavailable ------------------------------


async def test_explicit_unknown_doc_type_raises_11401(tmp_path):
    doc = tmp_path / 'x.pdf'
    doc.write_bytes(b'x' * 10)
    model = _patch_model()
    with (
        model,
        patch(f'{SERVICE}.DocExtractTemplateDao.aget_by_doc_type', new_callable=AsyncMock, return_value=None),
    ):
        with pytest.raises(DocExtractTypeUnavailableError):
            await _run(file_path=str(doc), doc_type='nope')


async def test_explicit_disabled_type_raises_11401(tmp_path):
    """A disabled template must not be usable via the explicit path either."""
    doc = tmp_path / 'x.pdf'
    doc.write_bytes(b'x' * 10)
    disabled = {**TEMPLATE, 'enabled': 0}
    model = _patch_model()
    with (
        model,
        patch(f'{SERVICE}.DocExtractTemplateDao.aget_by_doc_type', new_callable=AsyncMock,
              return_value=type('T', (), disabled)()),
    ):
        with pytest.raises(DocExtractTypeUnavailableError):
            await _run(file_path=str(doc), doc_type='business_license')


# --- precheck row 6/8: parser -------------------------------------------------


async def test_no_usable_parser_raises_11405(tmp_path):
    doc = tmp_path / 'x.pdf'
    doc.write_bytes(b'x' * 10)
    model = _patch_model()
    with (
        patch(f'{SERVICE}.ParseAdapter.run_sync_doc_extract', new_callable=AsyncMock,
              side_effect=DocExtractNoParserError()),
        model,
        patch(f'{SERVICE}.DocExtractTemplateDao.aget_by_doc_type', new_callable=AsyncMock,
              return_value=type('T', (), TEMPLATE)()),
    ):
        with pytest.raises(DocExtractNoParserError):
            await _run(file_path=str(doc), doc_type='business_license')


# --- ordering: first match wins ---------------------------------------------


async def test_oversize_beats_unconfigured_model(tmp_path):
    """Row 2 outranks row 3 -- so an oversize doc reports the size problem even
    when the model is also missing."""
    big = tmp_path / 'big.pdf'
    big.write_bytes(b'x' * (MAX_DOCUMENT_BYTES + 1))
    with patch(f'{SERVICE}.ExtractionService._resolve_model_id', return_value=None):
        with pytest.raises(DocExtractDocTooLargeError):
            await _run(file_path=str(big))


async def test_unreadable_beats_oversize_unconfigured(tmp_path):
    """Row 1 outranks everything."""
    with patch(f'{SERVICE}.ExtractionService._resolve_model_id', return_value=None):
        with pytest.raises(DocExtractDocUnreadableError):
            await _run(file_path='/nope.pdf')


# --- precheck rejections leave no record ------------------------------------


async def test_rejected_request_writes_no_run_record(tmp_path):
    """spec AC-02: nothing entered extraction, so there is nothing to record."""
    big = tmp_path / 'big.pdf'
    big.write_bytes(b'x' * (MAX_DOCUMENT_BYTES + 1))
    with patch(f'{SERVICE}.DocExtractRunDao.acreate', new_callable=AsyncMock) as create:
        with pytest.raises(DocExtractDocTooLargeError):
            await _run(file_path=str(big))
    create.assert_not_awaited()


# --- extraction: validation + retry -----------------------------------------


async def test_successful_extraction_returns_fields_and_markdown(tmp_path):
    doc = tmp_path / 'x.pdf'
    doc.write_bytes(b'x' * 10)
    model = _patch_model('{"company_name": "广西某某有限公司"}')
    with (
        _patch_ok_parse(), model,
        patch(f'{SERVICE}.DocExtractTemplateDao.aget_by_doc_type', new_callable=AsyncMock,
              return_value=type('T', (), TEMPLATE)()),
    ):
        result = await _run(file_path=str(doc), doc_type='business_license')

    assert result.ok is True
    assert result.fields == {'company_name': '广西某某有限公司'}
    assert result.markdown == '名称：广西某某有限公司'
    assert result.route == 'text'
    assert result.determined_by == 'explicit'


async def test_missing_required_field_retries_then_fails_11407(tmp_path):
    doc = tmp_path / 'x.pdf'
    doc.write_bytes(b'x' * 10)
    model = _patch_model('{}')  # never supplies the required field
    with (
        _patch_ok_parse(), model,
        patch(f'{SERVICE}.DocExtractTemplateDao.aget_by_doc_type', new_callable=AsyncMock,
              return_value=type('T', (), TEMPLATE)()),
    ):
        with pytest.raises(DocExtractFieldInvalidError) as exc:
            await _run(file_path=str(doc), doc_type='business_license')

    assert 'company_name' in str(exc.value)
    # one initial attempt + retries
    assert model.invoke.await_count == MAX_RETRIES + 1


async def test_retry_succeeds_on_second_attempt(tmp_path):
    doc = tmp_path / 'x.pdf'
    doc.write_bytes(b'x' * 10)
    calls = {'n': 0}

    async def _flaky(*args, **kwargs):
        calls['n'] += 1
        if calls['n'] == 1:
            return '{}'
        return '{"company_name": "广西某某有限公司"}'

    model = _patch_model_failing(_flaky)
    with (
        _patch_ok_parse(), model,
        patch(f'{SERVICE}.DocExtractTemplateDao.aget_by_doc_type', new_callable=AsyncMock,
              return_value=type('T', (), TEMPLATE)()),
    ):
        result = await _run(file_path=str(doc), doc_type='business_license')

    assert result.ok is True
    assert calls['n'] == 2


async def test_extra_fields_are_ignored_and_pass(tmp_path):
    """AC-27: an unexpected key is dropped, not treated as a failure."""
    doc = tmp_path / 'x.pdf'
    doc.write_bytes(b'x' * 10)
    model = _patch_model('{"company_name": "A", "surprise": "ignored"}')
    with (
        _patch_ok_parse(), model,
        patch(f'{SERVICE}.DocExtractTemplateDao.aget_by_doc_type', new_callable=AsyncMock,
              return_value=type('T', (), TEMPLATE)()),
    ):
        result = await _run(file_path=str(doc), doc_type='business_license')

    assert result.ok is True
    assert 'surprise' not in result.fields
    assert model.invoke.await_count == 1  # no retry triggered


async def test_model_call_failure_does_not_retry(tmp_path):
    """AC-24: a transport failure is not a format problem -- retrying it just
    multiplies the delay."""
    doc = tmp_path / 'x.pdf'
    doc.write_bytes(b'x' * 10)
    with (
        _patch_ok_parse(),
        _patch_model_failing(RuntimeError('connection reset')),
        patch(f'{SERVICE}.DocExtractTemplateDao.aget_by_doc_type', new_callable=AsyncMock,
              return_value=type('T', (), TEMPLATE)()),
    ):
        with pytest.raises(DocExtractContentFetchError):
            await _run(file_path=str(doc), doc_type='business_license')


async def test_malformed_json_from_model_retries(tmp_path):
    """Non-JSON output IS a format problem, so it does get retried."""
    doc = tmp_path / 'x.pdf'
    doc.write_bytes(b'x' * 10)
    model = _patch_model('not json')
    with (
        _patch_ok_parse(), model,
        patch(f'{SERVICE}.DocExtractTemplateDao.aget_by_doc_type', new_callable=AsyncMock,
              return_value=type('T', (), TEMPLATE)()),
    ):
        with pytest.raises(DocExtractFieldInvalidError):
            await _run(file_path=str(doc), doc_type='business_license')

    assert model.invoke.await_count == MAX_RETRIES + 1


# --- dry_run ----------------------------------------------------------------


async def test_dry_run_writes_no_record(tmp_path):
    """AC-34: trial extraction never persists."""
    doc = tmp_path / 'x.pdf'
    doc.write_bytes(b'x' * 10)
    model = _patch_model()
    with (
        _patch_ok_parse(), model,
        patch(f'{SERVICE}.DocExtractTemplateDao.aget_by_id', new_callable=AsyncMock,
              return_value=type('T', (), TEMPLATE)()),
        patch(f'{SERVICE}.DocExtractRunDao.acreate', new_callable=AsyncMock) as create,
    ):
        await _run(file_path=str(doc), dry_run=True, template_id=12, trial_definition={
            'field_schema': SCHEMA, 'render_template': '名称：{{company_name}}', 'description': 'd',
        })
    create.assert_not_awaited()


async def test_business_run_writes_record(tmp_path):
    doc = tmp_path / 'x.pdf'
    doc.write_bytes(b'x' * 10)
    model = _patch_model()
    with (
        _patch_ok_parse(), model,
        patch(f'{SERVICE}.DocExtractTemplateDao.aget_by_doc_type', new_callable=AsyncMock,
              return_value=type('T', (), TEMPLATE)()),
        patch(f'{SERVICE}.DocExtractRunDao.acreate', new_callable=AsyncMock) as create,
    ):
        await _run(file_path=str(doc), doc_type='business_license')

    create.assert_awaited_once()
    payload = create.await_args.args[0]
    assert payload['doc_type'] == 'business_license'
    assert payload['determined_by'] == 'explicit'
    assert payload['status'] == 1


async def test_business_run_failure_writes_failed_record(tmp_path):
    """spec AC-03: failures after extraction has started are recorded too."""
    doc = tmp_path / 'x.pdf'
    doc.write_bytes(b'x' * 10)
    model = _patch_model('{}')
    with (
        _patch_ok_parse(), model,
        patch(f'{SERVICE}.DocExtractTemplateDao.aget_by_doc_type', new_callable=AsyncMock,
              return_value=type('T', (), TEMPLATE)()),
        patch(f'{SERVICE}.DocExtractRunDao.acreate', new_callable=AsyncMock) as create,
    ):
        with pytest.raises(DocExtractFieldInvalidError):
            await _run(file_path=str(doc), doc_type='business_license')

    create.assert_awaited_once()
    payload = create.await_args.args[0]
    assert payload['status'] == 0
    assert payload['error_code'] == 11407


async def test_trial_definition_is_used_instead_of_saved_one(tmp_path):
    """AC-41: trial runs against what is in the editor, not what is stored."""
    doc = tmp_path / 'x.pdf'
    doc.write_bytes(b'x' * 10)
    editor_schema = [{'key': 'legal_person', 'label': '法定代表人', 'type': 'string', 'required': True}]
    model = _patch_model('{"legal_person": "张三"}')
    with (
        _patch_ok_parse(), model,
        patch(f'{SERVICE}.DocExtractTemplateDao.aget_by_id', new_callable=AsyncMock,
              return_value=type('T', (), TEMPLATE)()),
    ):
        result = await _run(
            file_path=str(doc), dry_run=True, template_id=12,
            trial_definition={
                'field_schema': editor_schema,
                'render_template': '法人：{{legal_person}}',
                'description': 'editor',
            },
        )

    assert result.fields == {'legal_person': '张三'}
    assert result.markdown == '法人：张三'


async def test_trial_without_template_id_raises_11411(tmp_path):
    """AC-43: a trial run needs a template to attribute the result to.

    The model slot is stubbed because the precheck table checks it first (row 3
    before row 5) -- without a configured model the request never reaches the
    template check.
    """
    doc = tmp_path / 'x.pdf'
    doc.write_bytes(b'x' * 10)
    with patch(f'{SERVICE}.ExtractionService._resolve_model_id', return_value=99):
        with pytest.raises(DocExtractTemplateMissingError):
            await _run(file_path=str(doc), dry_run=True, template_id=None)


async def test_trial_with_deleted_template_raises_11411(tmp_path):
    doc = tmp_path / 'x.pdf'
    doc.write_bytes(b'x' * 10)
    with (
        patch(f'{SERVICE}.ExtractionService._resolve_model_id', return_value=99),
        patch(f'{SERVICE}.DocExtractTemplateDao.aget_by_id', new_callable=AsyncMock, return_value=None),
    ):
        with pytest.raises(DocExtractTemplateMissingError):
            await _run(file_path=str(doc), dry_run=True, template_id=12, trial_definition={
                'field_schema': SCHEMA, 'render_template': '', 'description': 'd',
            })


async def test_invalid_trial_definition_rejected(tmp_path):
    """AC-42: a definition that could not be saved cannot be run either."""
    from bisheng.common.errcode.doc_extract import DocExtractRenderRefInvalidError

    doc = tmp_path / 'x.pdf'
    doc.write_bytes(b'x' * 10)
    with (
        patch(f'{SERVICE}.ExtractionService._resolve_model_id', return_value=99),
        patch(f'{SERVICE}.DocExtractTemplateDao.aget_by_id', new_callable=AsyncMock,
              return_value=type('T', (), TEMPLATE)()),
    ):
        with pytest.raises(DocExtractRenderRefInvalidError):
            await _run(file_path=str(doc), dry_run=True, template_id=12, trial_definition={
                'field_schema': SCHEMA,
                'render_template': '{{not_a_field}}',
                'description': 'd',
            })
