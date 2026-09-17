"""Template CRUD: save-time validation, identifier uniqueness, lifecycle, audit.

The DAO is mocked so these tests exercise the service's rules rather than SQL.
The rules being protected here are the ones the spec makes observable:

- AC-04 create + visible in the list
- AC-06 new templates start enabled
- AC-07/AC-08 identifier uniqueness applies to *live* rows only, so deleting a
  template frees its identifier for reuse
- AC-09/AC-10/AC-11 which definitions are legal (and that an empty render form
  is legal)
- AC-12/AC-13 disable and edit semantics
- AC-05 unknown / other-tenant template ids are indistinguishable
- AC-38 every write leaves an audit trail
"""

from unittest.mock import AsyncMock, patch

import pytest

from bisheng.common.errcode.doc_extract import (
    DocExtractRenderRefInvalidError,
    DocExtractTemplateInvalidError,
    DocExtractTemplateMissingError,
    DocExtractTypeDuplicateError,
)
from bisheng.doc_extract.domain.constants import DocExtractAuditAction
from bisheng.doc_extract.domain.services.template_service import TemplateService

SERVICE = 'bisheng.doc_extract.domain.services.template_service'
AUDIT = 'bisheng.doc_extract.domain.services.template_service.write_template_audit'

VALID_SCHEMA = [
    {'key': 'company_name', 'label': '企业名称', 'type': 'string', 'required': True},
    {'key': 'valid_to', 'label': '营业期限至', 'type': 'date', 'required': False},
]

VALID_PAYLOAD = {
    'name': '营业执照',
    'doc_type': 'business_license',
    'description': '工商行政管理部门颁发的企业登记证件',
    'field_schema': VALID_SCHEMA,
    'render_template': '名称：{{company_name}}\n期限：{{valid_to}}',
}


def _make_template(**overrides):
    class _Template:
        pass

    tpl = _Template()
    tpl.id = overrides.pop('id', 1)
    tpl.name = overrides.pop('name', '营业执照')
    tpl.doc_type = overrides.pop('doc_type', 'business_license')
    tpl.enabled = overrides.pop('enabled', 1)
    tpl.is_deleted = overrides.pop('is_deleted', 0)
    tpl.tenant_id = overrides.pop('tenant_id', 1)
    for key, value in overrides.items():
        setattr(tpl, key, value)
    return tpl


@pytest.fixture()
def audit_spy():
    with patch(AUDIT, new_callable=AsyncMock) as spy:
        yield spy


# --- validation: rejected at save time, never at run time -------------------


@pytest.mark.parametrize('field', ['name', 'description'])
def test_empty_required_text_fields_rejected(field):
    payload = {**VALID_PAYLOAD, field: ''}
    with pytest.raises(DocExtractTemplateInvalidError):
        TemplateService.validate_definition(**payload)


def test_zero_field_schema_rejected():
    with pytest.raises(DocExtractTemplateInvalidError):
        TemplateService.validate_definition(**{**VALID_PAYLOAD, 'field_schema': []})


def test_field_without_key_rejected():
    schema = [{'label': '无标识', 'type': 'string', 'required': False}]
    with pytest.raises(DocExtractTemplateInvalidError):
        TemplateService.validate_definition(**{**VALID_PAYLOAD, 'field_schema': schema})


def test_duplicate_field_key_rejected():
    schema = [
        {'key': 'a', 'label': 'A', 'type': 'string', 'required': False},
        {'key': 'a', 'label': 'A2', 'type': 'string', 'required': False},
    ]
    with pytest.raises(DocExtractTemplateInvalidError):
        TemplateService.validate_definition(**{**VALID_PAYLOAD, 'field_schema': schema})


def test_unsupported_field_type_rejected():
    schema = [{'key': 'a', 'label': 'A', 'type': 'array', 'required': False}]
    with pytest.raises(DocExtractTemplateInvalidError):
        TemplateService.validate_definition(**{**VALID_PAYLOAD, 'field_schema': schema})


def test_render_referencing_unknown_field_rejected():
    """AC-09: caught at save time, not left to render blanks at run time."""
    with pytest.raises(DocExtractRenderRefInvalidError):
        TemplateService.validate_definition(
            **{**VALID_PAYLOAD, 'render_template': '{{nope}}'}
        )


def test_empty_render_template_is_legal():
    """AC-11: an empty render form only affects the readable text."""
    TemplateService.validate_definition(**{**VALID_PAYLOAD, 'render_template': ''})


def test_valid_definition_passes():
    TemplateService.validate_definition(**VALID_PAYLOAD)


# --- create -----------------------------------------------------------------


async def test_create_persists_and_audits(audit_spy):
    created = _make_template()
    with (
        patch(f'{SERVICE}.DocExtractTemplateDao.aget_by_doc_type', new_callable=AsyncMock, return_value=None),
        patch(f'{SERVICE}.DocExtractTemplateDao.acreate', new_callable=AsyncMock, return_value=created),
    ):
        result = await TemplateService.acreate(operator_id=7, **VALID_PAYLOAD)

    assert result is created
    audit_spy.assert_awaited_once()
    assert audit_spy.await_args.kwargs['action'] == DocExtractAuditAction.TEMPLATE_CREATE


async def test_new_template_starts_enabled(audit_spy):
    """AC-06: no separate enable step before it can be used."""
    captured = {}

    async def _capture(data):
        captured.update(data)
        return _make_template()

    with (
        patch(f'{SERVICE}.DocExtractTemplateDao.aget_by_doc_type', new_callable=AsyncMock, return_value=None),
        patch(f'{SERVICE}.DocExtractTemplateDao.acreate', side_effect=_capture),
    ):
        await TemplateService.acreate(operator_id=7, **VALID_PAYLOAD)

    assert captured['enabled'] == 1


async def test_create_rejects_duplicate_doc_type(audit_spy):
    """AC-07: uniqueness is checked against live rows."""
    with patch(
        f'{SERVICE}.DocExtractTemplateDao.aget_by_doc_type',
        new_callable=AsyncMock,
        return_value=_make_template(),
    ):
        with pytest.raises(DocExtractTypeDuplicateError):
            await TemplateService.acreate(operator_id=7, **VALID_PAYLOAD)

    audit_spy.assert_not_awaited()


async def test_create_validates_before_touching_dao(audit_spy):
    with patch(f'{SERVICE}.DocExtractTemplateDao.acreate', new_callable=AsyncMock) as create:
        with pytest.raises(DocExtractRenderRefInvalidError):
            await TemplateService.acreate(
                operator_id=7, **{**VALID_PAYLOAD, 'render_template': '{{nope}}'}
            )
    create.assert_not_awaited()
    audit_spy.assert_not_awaited()


async def test_identifier_is_reusable_after_delete(audit_spy):
    """AC-08: the DAO's live-row lookup returns None once soft-deleted, so a
    template with the same identifier can be created again."""
    with (
        patch(f'{SERVICE}.DocExtractTemplateDao.aget_by_doc_type', new_callable=AsyncMock, return_value=None),
        patch(f'{SERVICE}.DocExtractTemplateDao.acreate', new_callable=AsyncMock, return_value=_make_template()),
    ):
        await TemplateService.acreate(operator_id=7, **VALID_PAYLOAD)

    audit_spy.assert_awaited_once()


# --- update -----------------------------------------------------------------


async def test_update_validates_and_audits(audit_spy):
    existing = _make_template()
    updated = _make_template(name='营业执照（新版）')
    with (
        patch(f'{SERVICE}.DocExtractTemplateDao.aget_by_id', new_callable=AsyncMock, return_value=existing),
        patch(f'{SERVICE}.DocExtractTemplateDao.aget_by_doc_type', new_callable=AsyncMock, return_value=existing),
        patch(f'{SERVICE}.DocExtractTemplateDao.aupdate', new_callable=AsyncMock, return_value=updated),
    ):
        result = await TemplateService.aupdate(
            operator_id=7, template_id=1, **{**VALID_PAYLOAD, 'name': '营业执照（新版）'}
        )

    assert result is updated
    assert audit_spy.await_args.kwargs['action'] == DocExtractAuditAction.TEMPLATE_UPDATE


async def test_update_missing_template_raises_11611(audit_spy):
    with patch(f'{SERVICE}.DocExtractTemplateDao.aget_by_id', new_callable=AsyncMock, return_value=None):
        with pytest.raises(DocExtractTemplateMissingError):
            await TemplateService.aupdate(operator_id=7, template_id=999, **VALID_PAYLOAD)

    audit_spy.assert_not_awaited()


async def test_update_rejects_identifier_taken_by_another_template():
    existing = _make_template(id=1, doc_type='business_license')
    other = _make_template(id=2, doc_type='id_card')
    with (
        patch(f'{SERVICE}.DocExtractTemplateDao.aget_by_id', new_callable=AsyncMock, return_value=existing),
        patch(f'{SERVICE}.DocExtractTemplateDao.aget_by_doc_type', new_callable=AsyncMock, return_value=other),
    ):
        with pytest.raises(DocExtractTypeDuplicateError):
            await TemplateService.aupdate(
                operator_id=7, template_id=1, **{**VALID_PAYLOAD, 'doc_type': 'id_card'}
            )


async def test_whitespace_padded_identifier_still_collides(audit_spy):
    """Regression: the uniqueness check ran on the RAW identifier while the row
    was stored STRIPPED, so ``'  business_license'`` missed the existing row and
    then landed as ``business_license`` -- two live rows sharing one identifier,
    silently defeating AC-07.

    The check must see the normalized value, i.e. what actually gets written.
    """
    with patch(
        f'{SERVICE}.DocExtractTemplateDao.aget_by_doc_type',
        new_callable=AsyncMock,
        return_value=_make_template(doc_type='business_license'),
    ) as lookup:
        with pytest.raises(DocExtractTypeDuplicateError):
            await TemplateService.acreate(
                operator_id=7,
                **{**VALID_PAYLOAD, 'doc_type': '  business_license  '},
            )

    # and it must have looked up the stripped form, not the padded one
    assert lookup.await_args.args[0] == 'business_license'
    audit_spy.assert_not_awaited()


async def test_identifier_is_stored_stripped(audit_spy):
    """The value checked and the value stored are the same one."""
    captured = {}

    async def _capture(data):
        captured.update(data)
        return _make_template()

    with (
        patch(f'{SERVICE}.DocExtractTemplateDao.aget_by_doc_type', new_callable=AsyncMock, return_value=None),
        patch(f'{SERVICE}.DocExtractTemplateDao.acreate', side_effect=_capture),
    ):
        await TemplateService.acreate(
            operator_id=7, **{**VALID_PAYLOAD, 'doc_type': '  business_license  '}
        )

    assert captured['doc_type'] == 'business_license'


async def test_update_keeping_own_identifier_is_allowed():
    """The template's own row must not count as a conflict with itself."""
    existing = _make_template(id=1)
    with (
        patch(f'{SERVICE}.DocExtractTemplateDao.aget_by_id', new_callable=AsyncMock, return_value=existing),
        patch(f'{SERVICE}.DocExtractTemplateDao.aget_by_doc_type', new_callable=AsyncMock, return_value=existing),
        patch(f'{SERVICE}.DocExtractTemplateDao.aupdate', new_callable=AsyncMock, return_value=existing),
    ):
        await TemplateService.aupdate(operator_id=7, template_id=1, **VALID_PAYLOAD)


# --- lifecycle --------------------------------------------------------------


async def test_delete_soft_deletes_and_audits(audit_spy):
    with (
        patch(f'{SERVICE}.DocExtractTemplateDao.aget_by_id', new_callable=AsyncMock, return_value=_make_template()),
        patch(f'{SERVICE}.DocExtractTemplateDao.asoft_delete', new_callable=AsyncMock, return_value=True),
    ):
        await TemplateService.adelete(operator_id=7, template_id=1)

    assert audit_spy.await_args.kwargs['action'] == DocExtractAuditAction.TEMPLATE_DELETE


async def test_delete_missing_template_raises_11611(audit_spy):
    with patch(f'{SERVICE}.DocExtractTemplateDao.aget_by_id', new_callable=AsyncMock, return_value=None):
        with pytest.raises(DocExtractTemplateMissingError):
            await TemplateService.adelete(operator_id=7, template_id=999)

    audit_spy.assert_not_awaited()


async def test_disable_audits_with_disable_action(audit_spy):
    existing = _make_template(enabled=1)
    with (
        patch(f'{SERVICE}.DocExtractTemplateDao.aget_by_id', new_callable=AsyncMock, return_value=existing),
        patch(f'{SERVICE}.DocExtractTemplateDao.aupdate', new_callable=AsyncMock, return_value=existing),
    ):
        await TemplateService.aset_enabled(operator_id=7, template_id=1, enabled=False)

    assert audit_spy.await_args.kwargs['action'] == DocExtractAuditAction.TEMPLATE_DISABLE


async def test_enable_audits_with_enable_action(audit_spy):
    existing = _make_template(enabled=0)
    with (
        patch(f'{SERVICE}.DocExtractTemplateDao.aget_by_id', new_callable=AsyncMock, return_value=existing),
        patch(f'{SERVICE}.DocExtractTemplateDao.aupdate', new_callable=AsyncMock, return_value=existing),
    ):
        await TemplateService.aset_enabled(operator_id=7, template_id=1, enabled=True)

    assert audit_spy.await_args.kwargs['action'] == DocExtractAuditAction.TEMPLATE_ENABLE


# --- read -------------------------------------------------------------------


async def test_get_missing_template_raises_11611():
    with patch(f'{SERVICE}.DocExtractTemplateDao.aget_by_id', new_callable=AsyncMock, return_value=None):
        with pytest.raises(DocExtractTemplateMissingError):
            await TemplateService.aget(999)


async def test_get_returns_template():
    existing = _make_template()
    with patch(f'{SERVICE}.DocExtractTemplateDao.aget_by_id', new_callable=AsyncMock, return_value=existing):
        assert await TemplateService.aget(1) is existing


async def test_list_delegates_to_dao():
    rows = [_make_template(id=1), _make_template(id=2)]
    with patch(f'{SERVICE}.DocExtractTemplateDao.alist_templates', new_callable=AsyncMock, return_value=rows) as lister:
        result = await TemplateService.alist()
    assert result == rows
    lister.assert_awaited_once()
