"""Classification and extraction endpoints.

Authentication is deliberately split by mode:

- Both endpoints admit any signed-in user, because business extraction (M-1a)
  is for callers, not administrators (spec AC-37).
- A trial run (`dry_run=true`) additionally requires tenant-admin rights. That
  check happens *inside* the service, not in a `Depends`, because FastAPI's
  dependency chain cannot see the request body and so cannot branch on
  `dry_run`. Without it, any signed-in user could pass `dry_run=true` to probe
  templates they have no access to -- a privilege escalation, not a
  convenience gap (design decision 13).
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile

from fastapi import APIRouter, Depends, File, Form, UploadFile

from bisheng.common.dependencies.user_deps import UserPayload
from bisheng.common.schemas.api import resp_200
from bisheng.doc_extract.domain.services.extraction_service import ExtractionService

router = APIRouter(prefix='/doc_extract', tags=['doc-extract'])


async def _stage_upload(file: UploadFile) -> tuple[str, str]:
    """Write the upload to a temp file and return ``(path, original_name)``.

    Callers must clean up with ``_cleanup``.
    """
    tmp_dir = tempfile.mkdtemp(prefix='doc_extract_')
    name = file.filename or 'upload'
    path = os.path.join(tmp_dir, os.path.basename(name))
    with open(path, 'wb') as fh:
        shutil.copyfileobj(file.file, fh)
    return path, name


def _cleanup(file_path: str) -> None:
    parent = os.path.dirname(file_path)
    if parent and os.path.isdir(parent):
        shutil.rmtree(parent, ignore_errors=True)


def _result_payload(result) -> dict:
    return {
        'ok': True,
        'fields': result.fields,
        'markdown': result.markdown,
        'template': result.template,
        'confidence': result.confidence,
        'route': result.route,
        'determined_by': result.determined_by,
        'missing_fields': result.missing_fields,
    }


@router.post('/classify')
async def classify_document(
    file: UploadFile = File(...),
    login_user: UserPayload = Depends(UserPayload.get_login_user),
):
    """Return the candidate type and confidence without extracting.

    Uses the same precheck/parse/classify sequence as extraction; kept as its own
    endpoint for callers that only need to route a document.
    """
    from bisheng.doc_extract.domain.services.classifier_service import ClassifierService
    from bisheng.doc_extract.domain.services.extraction_service import (
        ExtractionService as ES,
    )

    path, name = await _stage_upload(file)
    try:
        size = ES._assert_readable(path)
        ES._assert_within_size(size)
        model_id = ES._resolve_model_id(None)
        from bisheng.doc_extract.domain.services.parse_adapter import ParseAdapter

        text, route = await ParseAdapter.run_sync_doc_extract(
            file_path=path, file_name=name, user_id=login_user.user_id
        )
        classified = await ClassifierService.aclassify(
            text, tenant_id=None, user_id=login_user.user_id, model_id=model_id
        )
        return resp_200({
            'doc_type': classified.doc_type,
            'confidence': classified.confidence,
            'template_id': classified.template.id,
            'route': route,
        })
    finally:
        _cleanup(path)


@router.post('/extract')
async def extract_document(
    file: UploadFile = File(...),
    doc_type: str | None = Form(default=None),
    dry_run: bool = Form(default=False),
    template_id: int | None = Form(default=None),
    trial_definition: str | None = Form(default=None),
    login_user: UserPayload = Depends(UserPayload.get_login_user),
):
    """Extract fields from a document.

    Business runs return HTTP error codes on failure so programmatic callers can
    branch on them. Trial runs return 200 with ``ok: false`` instead -- the admin
    is mid-tuning and an error toast on every failed attempt is noise. The one
    exception is the permission check, which must stay an HTTP error: a denied
    trial run is an escalation attempt, not a tuning step.
    """
    if dry_run:
        await ExtractionService.assert_trial_allowed(login_user)

    parsed_definition = None
    if trial_definition:
        parsed_definition = json.loads(trial_definition)

    path, name = await _stage_upload(file)
    try:
        if dry_run:
            try:
                result = await ExtractionService.extract(
                    file_path=path,
                    file_name=name,
                    user_id=login_user.user_id,
                    dry_run=True,
                    template_id=template_id,
                    trial_definition=parsed_definition,
                )
            except Exception as exc:
                # Trial failures are an expected part of tuning: report them in
                # the body rather than as a transport error.
                return resp_200({
                    'ok': False,
                    'error_code': getattr(exc, 'code', None),
                    'error_message': str(exc),
                    'reason': _reason_for(exc),
                })
            return resp_200(_result_payload(result))

        result = await ExtractionService.extract(
            file_path=path,
            file_name=name,
            user_id=login_user.user_id,
            doc_type=doc_type,
        )
        return resp_200(_result_payload(result))
    finally:
        _cleanup(path)


def _reason_for(exc: Exception) -> str:
    """Map an error to the ``reason`` token the UI switches on (spec AC-44).

    A stable token rather than a message, so the frontend does not have to match
    on human-readable text to decide what to show.
    """
    code = getattr(exc, 'code', None)
    return {
        11609: 'doc_unreadable',
        11610: 'over_size',
        11605: 'no_parser',
        11607: 'field_invalid',
        11611: 'template_missing',
        11601: 'type_unavailable',
        11606: 'type_unknown',
        11608: 'content_fetch_failed',
    }.get(code, 'unknown')
