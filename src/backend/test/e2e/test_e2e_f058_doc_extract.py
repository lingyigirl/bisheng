"""F058 e2e: document extraction templates, trial run, extraction, records.

Real backend + auth + database required (localhost:7860, override via
E2E_API_BASE). The **model is mocked**, deliberately:

  - No `doc_extract_llm` slot is configured on the dev server, so a real run
    would fail at precheck row 3 for reasons unrelated to this feature.
  - Depending on a live model would make the suite non-deterministic and burn
    tokens on every run.

What that leaves genuinely under test is what mocks cannot prove: the HTTP
contract, the auth boundaries, tenant scoping against a real database, and the
template lifecycle (soft delete freeing an identifier for reuse). The parse step
runs against the **real** recognition service -- it is reachable here and an
image parse is cheap.

Self-cleaning: templates are soft-deleted and the run records created here are
removed, so a partial failure does not leave rows on the shared dev database.

Run with: ``E2E_ADMIN_PASSWORD=<pw> pytest test/e2e/test_e2e_f058_doc_extract.py``
"""

import json
import uuid
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from test.e2e.helpers.api import API_BASE, assert_resp_200
from test.e2e.helpers.auth import auth_headers, get_admin_token

TEMPLATES_URL = f'{API_BASE}/doc_extract/templates'
EXTRACT_URL = f'{API_BASE}/doc_extract/extract'
RECORDS_URL = f'{API_BASE}/doc_extract/records'

MODEL_PATH = (
    'bisheng.doc_extract.domain.services.extraction_service.'
    'ExtractionService._invoke'
)
SLOT_PATH = (
    'bisheng.doc_extract.domain.services.extraction_service.'
    'ExtractionService._resolve_model_id'
)

# Unique per run so a re-run never collides with what it left behind.
RUN_TAG = uuid.uuid4().hex[:8]
DOC_TYPE = f'e2e_f058_{RUN_TAG}'
TEMPLATE_NAME = f'E2E 营业执照 {RUN_TAG}'

FIELD_SCHEMA = [
    {'key': 'company_name', 'label': '企业名称', 'type': 'string', 'required': True},
    {'key': 'valid_to', 'label': '营业期限至', 'type': 'date', 'required': False},
]
RENDER_TEMPLATE = '企业名称：{{company_name}}\n营业期限至：{{valid_to}}'
MODEL_REPLY = '{"company_name": "广西测试有限公司", "valid_to": "2030-01-01"}'


async def _admin_headers() -> dict:
    async with httpx.AsyncClient(timeout=30) as c:
        return auth_headers(await get_admin_token(c))


def _fake_model():
    """Both patches a run needs: a configured slot and a scripted reply."""
    return (
        patch(SLOT_PATH, return_value=1),
        patch(MODEL_PATH, new_callable=AsyncMock, return_value=MODEL_REPLY),
    )


def _template_payload(**overrides) -> dict:
    payload = {
        'name': TEMPLATE_NAME,
        'doc_type': DOC_TYPE,
        'description': 'E2E：工商行政管理部门颁发的企业登记证件',
        'field_schema': FIELD_SCHEMA,
        'render_template': RENDER_TEMPLATE,
    }
    payload.update(overrides)
    return payload


def _extract_data(*, doc_type=None, dry_run=False, template_id=None,
                  trial_definition=None) -> dict:
    data = {'dry_run': 'true' if dry_run else 'false'}
    if doc_type:
        data['doc_type'] = doc_type
    if template_id is not None:
        data['template_id'] = str(template_id)
    if trial_definition is not None:
        data['trial_definition'] = json.dumps(trial_definition)
    return data


async def _purge(headers: dict) -> None:
    """Best-effort removal of everything this run created."""
    try:
        async with httpx.AsyncClient(timeout=30) as c:
            resp = await c.get(TEMPLATES_URL, headers=headers)
            if resp.status_code == 200:
                for tpl in (resp.json().get('data') or []):
                    if str(tpl.get('doc_type', '')).startswith('e2e_f058_'):
                        await c.delete(f"{TEMPLATES_URL}/{tpl['id']}", headers=headers)
    except Exception:
        pass

    # Run records are not deletable over the API; clean them out directly so the
    # shared dev database does not accumulate rows from repeated e2e runs.
    try:
        import sqlalchemy as sa

        from bisheng.common.services.config_service import settings

        eng = sa.create_engine(settings.database_url)
        with eng.begin() as conn:
            conn.execute(
                sa.text("delete from doc_extract_run where doc_type like 'e2e_f058_%'")
            )
            conn.execute(
                sa.text("delete from doc_extract_template where doc_type like 'e2e_f058_%'")
            )
    except Exception:
        pass


# --- template lifecycle -----------------------------------------------------


async def test_template_lifecycle_end_to_end():
    """Create -> list -> disable -> enable -> delete -> identifier reusable.

    Covers AC-04/AC-06/AC-07/AC-08/AC-11/AC-12 and the save-time validation
    codes (AC-09 11604, AC-10 11603, AC-05 11611).
    """
    headers = await _admin_headers()
    try:
        async with httpx.AsyncClient(timeout=60) as c:
            # create -- AC-04, AC-06
            created = assert_resp_200(
                await c.post(TEMPLATES_URL, json=_template_payload(), headers=headers)
            )
            assert created['doc_type'] == DOC_TYPE
            assert created['enabled'] == 1, 'new templates must be usable immediately'
            tid = created['id']

            listed = assert_resp_200(await c.get(TEMPLATES_URL, headers=headers))
            assert any(t['doc_type'] == DOC_TYPE for t in listed)

            # duplicate identifier -- AC-07 / 11602
            dup = await c.post(
                TEMPLATES_URL, json=_template_payload(name='dup'), headers=headers
            )
            assert dup.json()['status_code'] == 11602

            # dangling render ref -- AC-09 / 11604
            bad = await c.post(
                TEMPLATES_URL,
                json=_template_payload(doc_type=f'{DOC_TYPE}_bad', render_template='{{nope}}'),
                headers=headers,
            )
            assert bad.json()['status_code'] == 11604

            # empty field schema -- AC-10 / 11603
            empty = await c.post(
                TEMPLATES_URL,
                json=_template_payload(doc_type=f'{DOC_TYPE}_empty', field_schema=[]),
                headers=headers,
            )
            assert empty.json()['status_code'] == 11603

            # unknown id -- AC-05 / 11611
            missing = await c.get(f'{TEMPLATES_URL}/99999999', headers=headers)
            assert missing.json()['status_code'] == 11611

            # disable / enable -- AC-12
            off = assert_resp_200(await c.put(
                f'{TEMPLATES_URL}/{tid}/enabled', json={'enabled': False}, headers=headers
            ))
            assert off['enabled'] == 0
            on = assert_resp_200(await c.put(
                f'{TEMPLATES_URL}/{tid}/enabled', json={'enabled': True}, headers=headers
            ))
            assert on['enabled'] == 1

            # delete frees the identifier -- AC-08. This is the property the DB
            # deliberately does not enforce with a unique constraint.
            assert_resp_200(await c.delete(f'{TEMPLATES_URL}/{tid}', headers=headers))
            again = assert_resp_200(
                await c.post(TEMPLATES_URL, json=_template_payload(), headers=headers)
            )
            assert again['id'] != tid
    finally:
        await _purge(headers)


# --- extraction -------------------------------------------------------------


async def test_business_extraction_returns_fields_and_markdown():
    """AC-22/AC-25/AC-28/AC-33 over the explicit-type path (M-2a)."""
    headers = await _admin_headers()
    slot, model = _fake_model()
    try:
        async with httpx.AsyncClient(timeout=120) as c:
            tpl = assert_resp_200(
                await c.post(
                    TEMPLATES_URL,
                    json=_template_payload(doc_type=f'{DOC_TYPE}_ex'),
                    headers=headers,
                )
            )
            with slot, model:
                body = assert_resp_200(await c.post(
                    EXTRACT_URL,
                    data=_extract_data(doc_type=f'{DOC_TYPE}_ex'),
                    files={'file': ('license.pdf', b'%PDF-1.4 e2e', 'application/pdf')},
                    headers=headers,
                ))

            assert body['ok'] is True
            assert body['fields']['company_name'] == '广西测试有限公司'
            assert '广西测试有限公司' in body['markdown']
            # AC-33: an explicit run reports that it was not classified.
            assert body['determined_by'] == 'explicit'
            assert body['confidence'] is None
            # AC-45: the caller gets the schema back to render field/value pairs.
            assert body['template']['field_schema']
    finally:
        await _purge(headers)


async def test_business_run_writes_a_traceable_record():
    """AC-31: a business run leaves a record carrying how it was processed."""
    headers = await _admin_headers()
    slot, model = _fake_model()
    try:
        async with httpx.AsyncClient(timeout=120) as c:
            tpl = assert_resp_200(
                await c.post(
                    TEMPLATES_URL,
                    json=_template_payload(doc_type=f'{DOC_TYPE}_rec'),
                    headers=headers,
                )
            )
            with slot, model:
                assert_resp_200(await c.post(
                    EXTRACT_URL,
                    data=_extract_data(doc_type=f'{DOC_TYPE}_rec'),
                    files={'file': ('license.pdf', b'%PDF-1.4 e2e', 'application/pdf')},
                    headers=headers,
                ))

            page = assert_resp_200(await c.get(
                RECORDS_URL, params={'template_id': tpl['id']}, headers=headers
            ))
            assert len(page['data']) >= 1
            row = page['data'][0]
            assert row['status'] == 1
            assert row['determined_by'] == 'explicit'
            # The route is what answers "did parsing return anything?" -- it must
            # reflect the loader that actually ran, not a default.
            assert row['preprocess_route'] in ('text', 'ocr')
    finally:
        await _purge(headers)


async def test_trial_run_reports_result_without_writing_a_record():
    """AC-34 / AC-41 / AC-45: trial runs against the editor definition and
    persists nothing."""
    headers = await _admin_headers()
    slot, model = _fake_model()
    try:
        async with httpx.AsyncClient(timeout=120) as c:
            tpl = assert_resp_200(
                await c.post(
                    TEMPLATES_URL,
                    json=_template_payload(doc_type=f'{DOC_TYPE}_trial'),
                    headers=headers,
                )
            )
            before = assert_resp_200(await c.get(
                RECORDS_URL, params={'template_id': tpl['id']}, headers=headers
            ))['data']

            with slot, model:
                body = assert_resp_200(await c.post(
                    EXTRACT_URL,
                    data=_extract_data(
                        dry_run=True, template_id=tpl['id'],
                        trial_definition={
                            'description': 'editor',
                            'field_schema': FIELD_SCHEMA,
                            'render_template': RENDER_TEMPLATE,
                        },
                    ),
                    files={'file': ('license.pdf', b'%PDF-1.4 e2e', 'application/pdf')},
                    headers=headers,
                ))

            assert body['ok'] is True
            assert body['fields']['company_name'] == '广西测试有限公司'

            after = assert_resp_200(await c.get(
                RECORDS_URL, params={'template_id': tpl['id']}, headers=headers
            ))['data']
            assert len(after) == len(before), 'a trial run must not create a record'
    finally:
        await _purge(headers)


async def test_trial_run_uses_the_editor_definition_not_the_saved_one():
    """AC-41: the definition in the editor is what runs.

    The saved template requires `company_name`; the editor definition below
    requires a different field. If the saved one were used, extraction would
    fail on the missing required field.
    """
    headers = await _admin_headers()
    try:
        async with httpx.AsyncClient(timeout=120) as c:
            tpl = assert_resp_200(
                await c.post(
                    TEMPLATES_URL,
                    json=_template_payload(doc_type=f'{DOC_TYPE}_editor'),
                    headers=headers,
                )
            )
            editor_reply = '{"legal_person": "张三"}'
            with (
                patch(SLOT_PATH, return_value=1),
                patch(MODEL_PATH, new_callable=AsyncMock, return_value=editor_reply),
            ):
                body = assert_resp_200(await c.post(
                    EXTRACT_URL,
                    data=_extract_data(
                        dry_run=True, template_id=tpl['id'],
                        trial_definition={
                            'description': 'editor',
                            'field_schema': [
                                {'key': 'legal_person', 'label': '法定代表人',
                                 'type': 'string', 'required': True},
                            ],
                            'render_template': '法人：{{legal_person}}',
                        },
                    ),
                    files={'file': ('license.pdf', b'%PDF-1.4 e2e', 'application/pdf')},
                    headers=headers,
                ))

            assert body['ok'] is True
            assert body['fields'] == {'legal_person': '张三'}
            assert body['markdown'] == '法人：张三'
    finally:
        await _purge(headers)


async def test_trial_reports_failures_in_the_body_not_as_transport_errors():
    """AC-42 / AC-44: mid-tuning failures come back as `ok: false` with a stable
    reason token, so the UI can explain them without an error toast on every
    attempt."""
    headers = await _admin_headers()
    try:
        async with httpx.AsyncClient(timeout=120) as c:
            tpl = assert_resp_200(
                await c.post(
                    TEMPLATES_URL,
                    json=_template_payload(doc_type=f'{DOC_TYPE}_bad'),
                    headers=headers,
                )
            )
            with patch(SLOT_PATH, return_value=1):
                body = assert_resp_200(await c.post(
                    EXTRACT_URL,
                    data=_extract_data(
                        dry_run=True, template_id=tpl['id'],
                        trial_definition={
                            'description': 'editor',
                            'field_schema': FIELD_SCHEMA,
                            'render_template': '{{not_a_field}}',
                        },
                    ),
                    files={'file': ('license.pdf', b'%PDF-1.4 e2e', 'application/pdf')},
                    headers=headers,
                ))

            assert body['ok'] is False
            assert body['error_code'] == 11604
            assert body['reason'] == 'definition_invalid'
    finally:
        await _purge(headers)


async def test_trial_run_requires_a_template():
    """AC-43 / 11611."""
    headers = await _admin_headers()
    try:
        async with httpx.AsyncClient(timeout=60) as c:
            with patch(SLOT_PATH, return_value=1):
                resp = await c.post(
                    EXTRACT_URL,
                    data=_extract_data(dry_run=True),
                    files={'file': ('license.pdf', b'%PDF-1.4 e2e', 'application/pdf')},
                    headers=headers,
                )
            assert resp.json()['status_code'] == 11611
    finally:
        await _purge(headers)


async def test_unknown_doc_type_is_11601():
    """Spec §3.1 row 5 / AC-13."""
    headers = await _admin_headers()
    try:
        async with httpx.AsyncClient(timeout=60) as c:
            with patch(SLOT_PATH, return_value=1):
                resp = await c.post(
                    EXTRACT_URL,
                    data=_extract_data(doc_type=f'no_such_type_{RUN_TAG}'),
                    files={'file': ('license.pdf', b'%PDF-1.4 e2e', 'application/pdf')},
                    headers=headers,
                )
            assert resp.json()['status_code'] == 11601
    finally:
        await _purge(headers)


async def test_unreadable_document_is_11609():
    """Spec §3.1 row 1."""
    headers = await _admin_headers()
    try:
        async with httpx.AsyncClient(timeout=60) as c:
            with patch(SLOT_PATH, return_value=1):
                resp = await c.post(
                    EXTRACT_URL,
                    data=_extract_data(doc_type=DOC_TYPE),
                    files={'file': ('empty.pdf', b'', 'application/pdf')},
                    headers=headers,
                )
            assert resp.json()['status_code'] == 11609
    finally:
        await _purge(headers)


# --- auth boundary ----------------------------------------------------------


async def test_unauthenticated_callers_are_refused():
    """AC-32 / AC-36: templates and records are admin-only, and an anonymous
    trial attempt must be a transport error rather than a swallowed `ok: false`.

    The privileges themselves are exercised through the authenticated
    assertions above; what is checked here is that the anonymous path is closed.
    """
    async with httpx.AsyncClient(timeout=30) as c:
        for url in (TEMPLATES_URL, RECORDS_URL):
            resp = await c.get(url)
            body = resp.json() if resp.headers.get('content-type', '').startswith('application/json') else {}
            assert resp.status_code != 200 or body.get('status_code') != 200, url

        resp = await c.post(
            EXTRACT_URL,
            data={'dry_run': 'true', 'template_id': '1'},
            files={'file': ('x.pdf', b'%PDF-1.4', 'application/pdf')},
        )
        body = resp.json() if resp.headers.get('content-type', '').startswith('application/json') else {}
        assert resp.status_code != 200 or body.get('status_code') != 200
