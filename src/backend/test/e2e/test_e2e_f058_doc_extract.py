"""F058 e2e: document extraction templates against the real stack.

Real backend + auth + database required (localhost:7860, override via
E2E_API_BASE).

**What this suite is for.** The API-level tests in `test/doc_extract/
test_doc_extract_api.py` already cover the HTTP contract with the model and DAO
stubbed, using an in-process ASGI transport -- that is where mocking works.
This suite exists for the properties those cannot reach:

  - real authentication (a real JWT through the real middleware), and
  - template lifecycle against a real database, in particular that a soft delete
    frees the identifier for reuse -- the one rule the schema deliberately does
    NOT enforce with a unique constraint.

**What it deliberately does not do.** It does not mock the model. An earlier
revision tried to, and the mocks silently did nothing: this suite talks to a
backend running in a *separate process*, so `unittest.mock.patch` in the test
process never touches it. The requests then failed at precheck row 3 ("no model
configured") instead of exercising the path under test -- a green-looking suite
asserting the wrong thing. Extraction paths that need a model are therefore
marked `requires_model` and skip unless the tenant actually has a
`doc_extract_llm` slot configured.

Self-cleaning: templates are soft-deleted and the rows this run creates are
removed, so a partial failure does not leave residue on the shared dev database.

Run with: ``E2E_ADMIN_PASSWORD=<pw> pytest test/e2e/test_e2e_f058_doc_extract.py``
"""

import uuid

import httpx
import pytest

from test.e2e.helpers.api import API_BASE, assert_resp_200
from test.e2e.helpers.auth import auth_headers, get_admin_token

TEMPLATES_URL = f'{API_BASE}/doc_extract/templates'
EXTRACT_URL = f'{API_BASE}/doc_extract/extract'
RECORDS_URL = f'{API_BASE}/doc_extract/records'

# Unique per run so a re-run never collides with what it left behind.
RUN_TAG = uuid.uuid4().hex[:8]
DOC_TYPE = f'e2e_f058_{RUN_TAG}'
TEMPLATE_NAME = f'E2E 营业执照 {RUN_TAG}'

FIELD_SCHEMA = [
    {'key': 'company_name', 'label': '企业名称', 'type': 'string', 'required': True},
    {'key': 'valid_to', 'label': '营业期限至', 'type': 'date', 'required': False},
]
RENDER_TEMPLATE = '企业名称：{{company_name}}\n营业期限至：{{valid_to}}'


def _model_slot_configured() -> bool:
    """Whether this tenant has a document-extraction model configured.

    Checked against the database because the extraction endpoints return 11608
    before doing anything else when it is missing -- without this guard the
    model-dependent cases would "fail" for a reason that says nothing about the
    code under test.
    """
    try:
        import sqlalchemy as sa

        from bisheng.common.services.config_service import settings

        eng = sa.create_engine(settings.database_url)
        with eng.connect() as conn:
            row = conn.execute(sa.text(
                "SELECT value FROM tenant_system_model_config "
                "WHERE tenant_id = 1 AND `key` = 'doc_extract_llm'"
            )).fetchone()
        return bool(row and row[0])
    except Exception:
        return False


requires_model = pytest.mark.skipif(
    not _model_slot_configured(),
    reason=(
        'tenant has no doc_extract_llm slot configured, so extraction stops at '
        'precheck row 3 (11608) before reaching the path under test; configure '
        'the slot to run these'
    ),
)


async def _admin_headers() -> dict:
    async with httpx.AsyncClient(timeout=30) as c:
        return auth_headers(await get_admin_token(c))


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
    import json

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

    # Run records are not deletable over the API; clean them out directly so
    # repeated e2e runs do not accumulate rows on the shared dev database.
    try:
        import sqlalchemy as sa

        from bisheng.common.services.config_service import settings

        eng = sa.create_engine(settings.database_url)
        with eng.begin() as conn:
            conn.execute(sa.text(
                "delete from doc_extract_run where doc_type like 'e2e_f058_%'"
            ))
            conn.execute(sa.text(
                "delete from doc_extract_template where doc_type like 'e2e_f058_%'"
            ))
    except Exception:
        pass


# --- template lifecycle (no model needed) -----------------------------------


async def test_template_lifecycle_end_to_end():
    """Create -> validate -> list -> disable -> enable -> delete -> reuse.

    Covers AC-04, AC-05, AC-06, AC-07, AC-08, AC-09, AC-10, AC-11, AC-12. The
    delete-then-recreate step is the one worth proving against a real database:
    it is the rule the schema deliberately leaves to the service layer.
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

            # dangling render reference -- AC-09 / 11604, rejected on save
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

            # empty render form is legal -- AC-11
            no_render = assert_resp_200(await c.post(
                TEMPLATES_URL,
                json=_template_payload(doc_type=f'{DOC_TYPE}_nr', render_template=''),
                headers=headers,
            ))
            assert no_render['render_template'] == ''

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

            # delete frees the identifier -- AC-08
            assert_resp_200(await c.delete(f'{TEMPLATES_URL}/{tid}', headers=headers))
            again = assert_resp_200(
                await c.post(TEMPLATES_URL, json=_template_payload(), headers=headers)
            )
            assert again['id'] != tid, 'a soft-deleted identifier must be reusable'
    finally:
        await _purge(headers)


async def test_deleted_template_disappears_from_the_list():
    """AC-08 / AC-06: a deleted template is gone, while its records remain.

    Read back from a *fresh request* rather than the delete response, so this
    asserts what the next caller sees.
    """
    headers = await _admin_headers()
    try:
        async with httpx.AsyncClient(timeout=60) as c:
            tpl = assert_resp_200(await c.post(
                TEMPLATES_URL,
                json=_template_payload(doc_type=f'{DOC_TYPE}_gone', name=f'gone {RUN_TAG}'),
                headers=headers,
            ))
            assert_resp_200(await c.delete(f"{TEMPLATES_URL}/{tpl['id']}", headers=headers))

            listed = assert_resp_200(await c.get(TEMPLATES_URL, headers=headers))
            assert not any(t['id'] == tpl['id'] for t in listed)

            # and it is gone by id too -- AC-05
            assert (await c.get(f"{TEMPLATES_URL}/{tpl['id']}", headers=headers)
                    ).json()['status_code'] == 11611
    finally:
        await _purge(headers)


async def test_unknown_template_type_is_not_usable_for_extraction():
    """Spec §3.1 row 5 / AC-13.

    This one runs without a model: when the slot is unconfigured the row-3 check
    fires first, so the assertion accepts either code and documents why.
    """
    headers = await _admin_headers()
    try:
        async with httpx.AsyncClient(timeout=60) as c:
            resp = await c.post(
                EXTRACT_URL,
                data=_extract_data(doc_type=f'no_such_type_{RUN_TAG}'),
                files={'file': ('license.pdf', b'%PDF-1.4 e2e', 'application/pdf')},
                headers=headers,
            )
            code = resp.json()['status_code']
            expected = 11601 if _model_slot_configured() else 11608
            assert code == expected, (
                f'expected {expected}; 11608 means the model slot is '
                f'unconfigured and row 3 short-circuits row 5'
            )
    finally:
        await _purge(headers)


async def test_unreadable_document_is_rejected_before_the_model():
    """Spec §3.1 row 1 outranks row 3 -- an empty upload is refused for being
    empty, regardless of whether a model is configured."""
    headers = await _admin_headers()
    try:
        async with httpx.AsyncClient(timeout=60) as c:
            resp = await c.post(
                EXTRACT_URL,
                data=_extract_data(doc_type=DOC_TYPE),
                files={'file': ('empty.pdf', b'', 'application/pdf')},
                headers=headers,
            )
            assert resp.json()['status_code'] == 11609
    finally:
        await _purge(headers)


# --- extraction (needs a configured model) ----------------------------------


@requires_model
async def test_business_extraction_returns_fields_and_markdown():
    """AC-22/AC-25/AC-28/AC-33 over the explicit-type path (M-2a).

    Asserts the contract shape only -- the field *values* come from whichever
    model the tenant configured, so asserting them would make this suite depend
    on that model's behaviour.
    """
    headers = await _admin_headers()
    try:
        async with httpx.AsyncClient(timeout=180) as c:
            assert_resp_200(await c.post(
                TEMPLATES_URL,
                json=_template_payload(doc_type=f'{DOC_TYPE}_ex'),
                headers=headers,
            ))
            body = assert_resp_200(await c.post(
                EXTRACT_URL,
                data=_extract_data(doc_type=f'{DOC_TYPE}_ex'),
                files={'file': ('license.pdf', b'%PDF-1.4 e2e', 'application/pdf')},
                headers=headers,
            ))

            assert body['ok'] is True
            assert set(body['fields']) <= {'company_name', 'valid_to'}
            # AC-33: an explicit run reports that it was not classified.
            assert body['determined_by'] == 'explicit'
            assert body['confidence'] is None
            # AC-45: the schema comes back for field/value rendering.
            assert body['template']['field_schema']
    finally:
        await _purge(headers)


@requires_model
async def test_business_run_writes_a_traceable_record():
    """AC-31: a business run leaves a record saying how the document was read."""
    headers = await _admin_headers()
    try:
        async with httpx.AsyncClient(timeout=180) as c:
            tpl = assert_resp_200(await c.post(
                TEMPLATES_URL,
                json=_template_payload(doc_type=f'{DOC_TYPE}_rec'),
                headers=headers,
            ))
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
            # The route must reflect the loader that actually ran -- this is what
            # answers "did parsing return anything?" without reading logs.
            assert row['preprocess_route'] in ('text', 'ocr')
    finally:
        await _purge(headers)


@requires_model
async def test_trial_run_reports_a_result_without_writing_a_record():
    """AC-34 / AC-41 / AC-45: trial runs the editor definition, persists nothing."""
    headers = await _admin_headers()
    try:
        async with httpx.AsyncClient(timeout=180) as c:
            tpl = assert_resp_200(await c.post(
                TEMPLATES_URL,
                json=_template_payload(doc_type=f'{DOC_TYPE}_trial'),
                headers=headers,
            ))
            before = assert_resp_200(await c.get(
                RECORDS_URL, params={'template_id': tpl['id']}, headers=headers
            ))['data']

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

            after = assert_resp_200(await c.get(
                RECORDS_URL, params={'template_id': tpl['id']}, headers=headers
            ))['data']
            assert len(after) == len(before), 'a trial run must not create a record'
    finally:
        await _purge(headers)


# --- auth boundary (no model needed) ----------------------------------------


async def test_unauthenticated_callers_are_refused():
    """AC-32 / AC-36: templates and records are admin-only, and an anonymous
    trial attempt must be a transport error rather than a swallowed `ok: false`
    -- otherwise an escalation attempt looks like a tuning failure.

    The privileged paths are exercised by the authenticated cases above; what is
    checked here is that the anonymous path is closed.
    """
    async with httpx.AsyncClient(timeout=30) as c:
        for url in (TEMPLATES_URL, RECORDS_URL):
            resp = await c.get(url)
            body = (resp.json()
                    if resp.headers.get('content-type', '').startswith('application/json')
                    else {})
            assert resp.status_code != 200 or body.get('status_code') != 200, url

        resp = await c.post(
            EXTRACT_URL,
            data={'dry_run': 'true', 'template_id': '1'},
            files={'file': ('x.pdf', b'%PDF-1.4', 'application/pdf')},
        )
        body = (resp.json()
                if resp.headers.get('content-type', '').startswith('application/json')
                else {})
        assert resp.status_code != 200 or body.get('status_code') != 200
