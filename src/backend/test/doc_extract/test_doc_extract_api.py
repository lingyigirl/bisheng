"""API-level behaviour: authentication boundaries and the trial/business split.

Walks the endpoints with the service layer stubbed, because what is under test
here is the *contract* -- who may call what, and which failures are HTTP errors
versus `ok: false` bodies -- not the extraction logic (covered by
test_extraction_service.py).

Covers AC-32 (records are admin-only), AC-36 (admin-only template management and
the dry_run gate), AC-37 (any signed-in user may run business extraction),
AC-44 (trial failures report a stable reason token) and AC-45 (trial results
carry the schema and the missing-field list).
"""

from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from bisheng.common.dependencies.user_deps import UserPayload
from bisheng.doc_extract.api.router import router as doc_extract_router

SERVICE = 'bisheng.doc_extract.domain.services.extraction_service'
TEMPLATE_SERVICE = 'bisheng.doc_extract.domain.services.template_service'

TEMPLATE_ROW = {
    'id': 12,
    'name': '营业执照',
    'doc_type': 'business_license',
    'description': 'desc',
    'field_schema': [{'key': 'company_name', 'label': '企业名称', 'type': 'string', 'required': True}],
    'render_template': '名称：{{company_name}}',
    'enabled': 1,
    'create_user': 7,
    'create_time': None,
    'update_time': None,
}


class _User:
    def __init__(self, user_id=7, admin=True):
        self.user_id = user_id
        self.user_name = f'u{user_id}'
        self._admin = admin

    async def has_tenant_admin(self, tenant_id):
        return self._admin


def _app(admin=True, user_id=7):
    from bisheng.main import _EXCEPTION_HANDLERS

    # Mirror production: without the same exception handlers, an error raised in
    # a dependency or endpoint would surface as a raised exception here and the
    # tests would be asserting against behaviour the real app never exhibits.
    app = FastAPI(exception_handlers=_EXCEPTION_HANDLERS)
    app.include_router(doc_extract_router, prefix='/api/v1')
    user = _User(user_id=user_id, admin=admin)

    async def _login():
        return user

    async def _admin_only():
        if not admin:
            from bisheng.common.errcode.llm_tenant import LLMModelSharedReadonlyError

            raise LLMModelSharedReadonlyError()
        return user

    app.dependency_overrides[UserPayload.get_login_user] = _login
    app.dependency_overrides[UserPayload.get_tenant_admin_user] = _admin_only
    return app


@pytest.fixture()
def child_tenant():
    """Set the JWT leaf tenant for the duration of one async test.

    Set directly rather than via the shared `tenant_context` fixture: that one
    resets through a ContextVar token, and a token created outside the running
    event loop's context cannot be reset inside it (ValueError on teardown).
    A real request always carries a leaf tenant, and the tenant-admin branch
    only applies to a real Child tenant -- Root admins come through the global
    super-admin branch instead.
    """
    from bisheng.core.context.tenant import current_tenant_id

    current_tenant_id.set(2)
    yield 2
    current_tenant_id.set(None)


@pytest.fixture()
async def admin_client():
    async with AsyncClient(transport=ASGITransport(app=_app(admin=True)), base_url='http://t') as c:
        yield c


@pytest.fixture()
async def user_client():
    async with AsyncClient(transport=ASGITransport(app=_app(admin=False)), base_url='http://t') as c:
        yield c


def _fake_template(**overrides):
    row = {**TEMPLATE_ROW, **overrides}
    return type('T', (), row)()


def _fake_result():
    from bisheng.doc_extract.domain.services.extraction_service import ExtractResult

    return ExtractResult(
        ok=True,
        fields={'company_name': 'A'},
        markdown='名称：A',
        template={'id': 12, 'doc_type': 'business_license', 'name': '营业执照',
                  'field_schema': TEMPLATE_ROW['field_schema']},
        confidence=0.9,
        route='text',
        determined_by='explicit',
        missing_fields=[],
    )


# --- templates --------------------------------------------------------------


async def test_non_admin_cannot_list_templates(user_client):
    resp = await user_client.get('/api/v1/doc_extract/templates')
    assert resp.status_code != 200 or resp.json().get('status_code') != 200


async def test_admin_can_list_templates(admin_client):
    with patch(f'{TEMPLATE_SERVICE}.TemplateService.alist', new_callable=AsyncMock,
               return_value=[_fake_template()]):
        resp = await admin_client.get('/api/v1/doc_extract/templates')
    assert resp.json()['status_code'] == 200
    assert resp.json()['data'][0]['doc_type'] == 'business_license'


async def test_non_admin_cannot_create_template(user_client):
    resp = await user_client.post('/api/v1/doc_extract/templates', json={
        'name': 'x', 'doc_type': 'y', 'description': 'd', 'field_schema': [],
        'render_template': '',
    })
    assert resp.status_code != 200 or resp.json().get('status_code') != 200


async def test_create_template_passes_payload_through(admin_client):
    with patch(f'{TEMPLATE_SERVICE}.TemplateService.acreate', new_callable=AsyncMock,
               return_value=_fake_template()) as create:
        await admin_client.post('/api/v1/doc_extract/templates', json={
            'name': '营业执照', 'doc_type': 'business_license', 'description': 'd',
            'field_schema': TEMPLATE_ROW['field_schema'], 'render_template': '名称：{{company_name}}',
        })
    assert create.await_args.kwargs['doc_type'] == 'business_license'


async def test_toggle_enabled_uses_subresource(admin_client):
    with patch(f'{TEMPLATE_SERVICE}.TemplateService.aset_enabled', new_callable=AsyncMock,
               return_value=_fake_template(enabled=0)) as setter:
        resp = await admin_client.put('/api/v1/doc_extract/templates/12/enabled', json={'enabled': False})
    assert setter.await_args.kwargs['enabled'] is False
    assert resp.json()['data']['enabled'] == 0


async def test_delete_template(admin_client):
    with patch(f'{TEMPLATE_SERVICE}.TemplateService.adelete', new_callable=AsyncMock) as deleter:
        await admin_client.delete('/api/v1/doc_extract/templates/12')
    deleter.assert_awaited_once()


# --- records (AC-32) --------------------------------------------------------


async def test_non_admin_cannot_read_records(user_client):
    resp = await user_client.get('/api/v1/doc_extract/records')
    assert resp.status_code != 200 or resp.json().get('status_code') != 200


async def test_admin_can_read_records_with_cursor(admin_client):
    run = type('R', (), {
        'id': 5, 'template_id': 12, 'doc_type': 'business_license',
        'determined_by': 'auto', 'confidence': 0.9, 'preprocess_route': 'text',
        'fields': {'company_name': 'A'}, 'markdown': 'm', 'status': 1,
        'error_code': None, 'error_message': None, 'user_id': 7, 'create_time': None,
    })()
    with patch(
        'bisheng.doc_extract.api.endpoints.records.DocExtractRunDao.alist_runs',
        new_callable=AsyncMock, return_value=([run], 5),
    ):
        resp = await admin_client.get('/api/v1/doc_extract/records?template_id=12&limit=20')
    body = resp.json()['data']
    assert body['data'][0]['id'] == 5
    assert body['next_cursor'] == 5


# --- extraction (AC-37 / AC-36) ---------------------------------------------


async def test_signed_in_user_can_run_business_extraction(user_client):
    """AC-37: business extraction is not admin-only."""
    with patch(f'{SERVICE}.ExtractionService.extract', new_callable=AsyncMock,
               return_value=_fake_result()):
        resp = await user_client.post(
            '/api/v1/doc_extract/extract',
            data={'doc_type': 'business_license'},
            files={'file': ('x.pdf', b'hello', 'application/pdf')},
        )
    assert resp.json()['status_code'] == 200
    assert resp.json()['data']['ok'] is True


async def test_trial_run_denied_for_non_admin(user_client):
    """AC-36 / decision 13: the dry_run gate is what stops a signed-in user from
    probing templates they cannot see. It must be an HTTP error, not an
    `ok: false` body -- otherwise the attempt is indistinguishable from a
    tuning failure and never surfaces anywhere."""
    resp = await user_client.post(
        '/api/v1/doc_extract/extract',
        data={'dry_run': 'true', 'template_id': '12'},
        files={'file': ('x.pdf', b'hello', 'application/pdf')},
    )
    assert resp.status_code != 200 or resp.json().get('status_code') != 200


async def test_trial_run_allowed_for_admin(admin_client, child_tenant):
    with patch(f'{SERVICE}.ExtractionService.extract', new_callable=AsyncMock,
               return_value=_fake_result()):
        resp = await admin_client.post(
            '/api/v1/doc_extract/extract',
            data={'dry_run': 'true', 'template_id': '12'},
            files={'file': ('x.pdf', b'hello', 'application/pdf')},
        )
    assert resp.json()['data']['ok'] is True


async def test_trial_failure_returns_ok_false_with_reason(admin_client, child_tenant):
    """AC-44: trial failures are expected during tuning, so they come back as a
    200 with a stable reason token rather than an error toast."""
    from bisheng.common.errcode.doc_extract import DocExtractNoParserError

    with patch(f'{SERVICE}.ExtractionService.extract', new_callable=AsyncMock,
               side_effect=DocExtractNoParserError()):
        resp = await admin_client.post(
            '/api/v1/doc_extract/extract',
            data={'dry_run': 'true', 'template_id': '12'},
            files={'file': ('x.pdf', b'hello', 'application/pdf')},
        )
    body = resp.json()
    assert body['status_code'] == 200
    assert body['data']['ok'] is False
    assert body['data']['reason'] == 'no_parser'
    assert body['data']['error_code'] == 11405


async def test_trial_success_returns_schema_and_missing_fields(admin_client, child_tenant):
    """AC-45: the UI needs the schema to render field/value pairs, and the
    missing list to tell 'the sample lacks this' from 'the field is
    misconfigured'."""
    result = _fake_result()
    result.missing_fields = ['valid_to']
    with patch(f'{SERVICE}.ExtractionService.extract', new_callable=AsyncMock, return_value=result):
        resp = await admin_client.post(
            '/api/v1/doc_extract/extract',
            data={'dry_run': 'true', 'template_id': '12'},
            files={'file': ('x.pdf', b'hello', 'application/pdf')},
        )
    data = resp.json()['data']
    assert data['missing_fields'] == ['valid_to']
    assert data['template']['field_schema']


async def test_business_failure_is_an_http_error(admin_client):
    """Business callers are programs: they branch on the code, so a failure must
    not be hidden inside a 200 body."""
    from bisheng.common.errcode.doc_extract import DocExtractNoParserError

    with patch(f'{SERVICE}.ExtractionService.extract', new_callable=AsyncMock,
               side_effect=DocExtractNoParserError()):
        resp = await admin_client.post(
            '/api/v1/doc_extract/extract',
            data={'doc_type': 'business_license'},
            files={'file': ('x.pdf', b'hello', 'application/pdf')},
        )
    assert resp.status_code != 200 or resp.json().get('status_code') != 200


async def test_classify_endpoint(admin_client):
    classified = type('C', (), {'doc_type': 'business_license', 'confidence': 0.9,
                                'template': _fake_template()})()
    with (
        patch(f'{SERVICE}.ExtractionService._resolve_model_id', return_value=99),
        patch('bisheng.doc_extract.domain.services.parse_adapter.ParseAdapter.run_sync_doc_extract',
              new_callable=AsyncMock, return_value=('text ' * 40, 'text')),
        patch('bisheng.doc_extract.domain.services.classifier_service.ClassifierService.aclassify',
              new_callable=AsyncMock, return_value=classified),
    ):
        resp = await admin_client.post(
            '/api/v1/doc_extract/classify',
            files={'file': ('x.pdf', b'hello', 'application/pdf')},
        )
    assert resp.json()['data']['doc_type'] == 'business_license'
