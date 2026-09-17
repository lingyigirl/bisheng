"""Extraction record lookup.

Admin-only because a record contains the extracted field values, which for
certificate documents is personal or corporate identifying information
(spec AC-32).
"""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends

from bisheng.common.dependencies.user_deps import UserPayload
from bisheng.common.schemas.api import resp_200
from bisheng.doc_extract.domain.models.extract_run import DocExtractRunDao

router = APIRouter(prefix='/doc_extract/records', tags=['doc-extract'])


def _dump(run) -> dict:
    return {
        'id': run.id,
        'template_id': run.template_id,
        'doc_type': run.doc_type,
        'determined_by': run.determined_by,
        'confidence': run.confidence,
        'preprocess_route': run.preprocess_route,
        'fields': run.fields or {},
        'markdown': run.markdown or '',
        'status': run.status,
        'error_code': run.error_code,
        'error_message': run.error_message,
        'user_id': run.user_id,
        'create_time': run.create_time,
    }


@router.get('')
async def list_records(
    template_id: int | None = None,
    status: int | None = None,
    start_time: datetime | None = None,
    end_time: datetime | None = None,
    cursor: int | None = None,
    limit: int = 20,
    login_user: UserPayload = Depends(UserPayload.get_tenant_admin_user),
):
    rows, next_cursor = await DocExtractRunDao.alist_runs(
        template_id=template_id,
        status=status,
        start_time=start_time,
        end_time=end_time,
        cursor=cursor,
        limit=limit,
    )
    return resp_200({
        'data': [_dump(r) for r in rows],
        'next_cursor': next_cursor,
    })
