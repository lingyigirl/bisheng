from fastapi import APIRouter, Depends, File, Form, Query, UploadFile

from bisheng.common.dependencies.super_admin import require_global_super_admin
from bisheng.common.dependencies.user_deps import UserPayload
from bisheng.common.errcode.platform_sensitive_word import PlatformWordNotFoundError
from bisheng.common.schemas.api import resp_200
from bisheng.sensitive_word.domain.services.platform_word_service import (
    ImportMode,
    PlatformWordService,
)

router = APIRouter(prefix='/platform-sensitive-words', tags=['platform-sensitive-words'])


@router.get('')
async def list_platform_words(
    *,
    keyword: str | None = Query(None, description='词条模糊搜索'),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    login_user: UserPayload = Depends(require_global_super_admin),
):
    """Page through the platform word library (super admin only, spec AC-01/AC-06)."""
    rows, total = await PlatformWordService.alist_words(
        keyword=keyword, page=page, page_size=page_size
    )
    return resp_200(
        {
            'data': [
                {
                    'id': row.id,
                    'word': row.word,
                    'source': row.source,
                    'create_time': row.create_time.isoformat() if row.create_time else None,
                }
                for row in rows
            ],
            'total': total,
        }
    )


@router.post('')
async def add_platform_word(
    *,
    word: str = Form(..., description='要新增的敏感词条'),
    login_user: UserPayload = Depends(require_global_super_admin),
):
    """Add one word (spec AC-01).

    A blank or already-present word is reported as `created=False` rather than an
    error: the operator's intent is satisfied either way, and the page only needs
    to know whether the list changed.
    """
    created = await PlatformWordService.add_word(word, operator_id=login_user.user_id)
    return resp_200({'created': created})


@router.delete('/{word_id}')
async def delete_platform_word(
    word_id: int,
    login_user: UserPayload = Depends(require_global_super_admin),
):
    deleted = await PlatformWordService.delete_word(word_id)
    if not deleted:
        # Already gone — e.g. two admins deleted the same row, or the list is
        # stale. Distinct from a parse failure so the page can just refresh.
        raise PlatformWordNotFoundError.http_exception()
    return resp_200({'deleted': True})


@router.post('/import/preview')
async def preview_import(
    *,
    file: UploadFile = File(..., description='词表文件: txt / csv / xlsx'),
    mode: ImportMode = Form(ImportMode.OVERWRITE),
    login_user: UserPayload = Depends(require_global_super_admin),
):
    """Parse the file and report what an import would change (spec AC-02/AC-03).

    Nothing is written here — the dialog shows these numbers and the operator
    confirms before `/import` runs. A malformed file surfaces as 11301 now rather
    than after the operator has committed to an overwrite.
    """
    raw = await file.read()
    parsed = PlatformWordService.parse_words_file(raw, file.filename or '')
    existing = await PlatformWordService.alist_all_words()
    preview = PlatformWordService.preview_import(parsed, mode, existing=existing)

    return resp_200(
        {
            'mode': mode.value,
            'incoming': preview.incoming,
            'added': len(preview.added),
            'removed': len(preview.removed),
            'duplicates': preview.duplicates,
            'dropped_overlong': preview.dropped_overlong,
            # A small sample so the dialog can show WHAT would change, not just
            # how much. The full sets are rarely useful and can be huge.
            'added_sample': preview.added[:20],
            'removed_sample': preview.removed[:20],
        }
    )


@router.post('/import')
async def execute_import(
    *,
    file: UploadFile = File(..., description='词表文件: txt / csv / xlsx'),
    mode: ImportMode = Form(ImportMode.OVERWRITE),
    login_user: UserPayload = Depends(require_global_super_admin),
):
    """Apply an import (spec AC-02/AC-05/AC-07).

    The file is re-parsed here rather than trusting a token from the preview
    call: between preview and confirm the operator may pick a different file, and
    re-parsing is cheap next to the cost of importing a word set nobody previewed.
    """
    raw = await file.read()
    parsed = PlatformWordService.parse_words_file(raw, file.filename or '')
    written = await PlatformWordService.import_words(
        parsed, mode, operator_id=login_user.user_id
    )
    return resp_200({'mode': mode.value, 'written': written})
