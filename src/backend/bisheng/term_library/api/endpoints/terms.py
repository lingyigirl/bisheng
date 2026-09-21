"""HTTP surface of the term library (F060, spec AC-16 ~ AC-18).

**The whole of the permission design is here**, and it is deliberately short:
read is gated on the `knowledge` menu key, write additionally on
`create_knowledge`. There is no per-term authorization, no FGA tuple and no
resource type — the library is tenant-level shared configuration, the same shape
as the knowledge menu it sits behind (design decision 2 / contract AC-31).

Two shapes worth knowing before editing:

  * **The id routes are constrained to digits** (`/{term_id:int}`). `/import` and
    `/import/preview` are literals, and an unconstrained `/{term_id}` registered
    first would swallow them — F061 shipped exactly that bug with
    `query-failures`, returning 200 with the wrong handler's body rather than a
    404. Registration order matters too: the literals are declared first.
  * **No `PermissionService` call anywhere in this package** (AC-31). Reading a
    term is not a controlled operation; a department filter on the list is
    display metadata and grants nothing (contract INV-17).
"""

# ruff: noqa: RUF001 -- field descriptions are Chinese text the UI shows;
# fullwidth punctuation is correct in them.

from __future__ import annotations

from fastapi import APIRouter, Depends, File, Form, Query, UploadFile
from pydantic import BaseModel, Field

from bisheng.common.dependencies.user_deps import UserPayload
from bisheng.common.schemas.api import resp_200
from bisheng.database.models.role_access import WebMenuResource
from bisheng.term_library.domain.services import term_importer, term_service
from bisheng.term_library.domain.services.term_importer import ImportMode

router = APIRouter(prefix='/term-library', tags=['term-library'])


class TermCreatePayload(BaseModel):
    standard_term: str = Field(..., min_length=1, max_length=255, description='标准词，租户内唯一')
    definition: str = Field(..., min_length=1, description='释义，不超过 200 字')
    aliases: list[str] = Field(default_factory=list, description='别名，可空')
    department_id: int | None = Field(default=None, description='归属部门，可空')


class TermUpdatePayload(TermCreatePayload):
    """A full replace, not a patch — see `term_service.update_term` for why."""


async def _assert_can_read(login_user: UserPayload) -> None:
    """Being able to open the knowledge page is what it takes to read the list."""
    await UserPayload.assert_effective_web_menu_contains(login_user.user_id, WebMenuResource.KNOWLEDGE.value)


async def _assert_can_write(login_user: UserPayload) -> None:
    """Every mutation needs `create_knowledge` on top of read.

    Checked here rather than inside the service: the service is called by the
    import paths too, and a gate that lives in the wrong layer is one an internal
    caller silently bypasses.
    """
    await _assert_can_read(login_user)
    await UserPayload.assert_effective_web_menu_contains(login_user.user_id, WebMenuResource.CREATE_KNOWLEDGE.value)


def _term_dict(term) -> dict:
    """One term as the list and the editor both render it.

    Spelled out field by field rather than dumping the ORM object: every column
    here is meant to reach the client, and a future column that is NOT meant to
    would otherwise ship itself.
    """
    return {
        'id': term.id,
        'standard_term': term.standard_term,
        'aliases': list(term.aliases or []),
        'definition': term.definition,
        'department_id': term.department_id,
        'create_user_id': term.create_user_id,
        'create_time': term.create_time,
        'update_time': term.update_time,
    }


@router.get('')
async def list_terms(
    *,
    department_id: int | None = Query(None, description='按归属部门筛选，含子部门'),
    include_sub_departments: bool = Query(True, description='部门筛选是否含下级部门'),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    login_user: UserPayload = Depends(UserPayload.get_login_user),
):
    """Page the library, newest first (spec AC-12 / AC-13).

    `department_id` narrows the LIST only. It is not a permission and it does not
    narrow what gets injected — a term filed under another department still
    reaches this tenant's conversations (contract INV-17).
    """
    await _assert_can_read(login_user)

    rows, total = await term_service.list_terms(
        department_id=department_id,
        include_sub_departments=include_sub_departments,
        page=page,
        page_size=page_size,
    )
    return resp_200(
        {
            'data': [_term_dict(row) for row in rows],
            'total': total,
            'page': page,
            'page_size': page_size,
        }
    )


# ── import: declared BEFORE the id routes, and hyphen-free literals ──────────


@router.post('/import/preview')
async def preview_import(
    *,
    file: UploadFile = File(..., description='词条文件: csv / xlsx，三列：标准词, 别名, 释义'),
    mode: ImportMode = Form(ImportMode.APPEND),
    login_user: UserPayload = Depends(UserPayload.get_login_user),
):
    """Report what an import would change, writing nothing (spec AC-07 / AC-09).

    The numbers the dialog shows are produced by the code that would perform the
    import, so a file that turns out to be unusable fails here rather than after
    an overwrite has been confirmed.
    """
    await _assert_can_write(login_user)

    raw = await file.read()
    preview = await term_importer.preview_import(raw, file.filename or '', mode=mode)
    return resp_200({'mode': mode.value, **_preview_dict(preview)})


@router.post('/import')
async def execute_import(
    *,
    file: UploadFile = File(..., description='词条文件: csv / xlsx，三列：标准词, 别名, 释义'),
    mode: ImportMode = Form(ImportMode.APPEND),
    login_user: UserPayload = Depends(UserPayload.get_login_user),
):
    """Apply an import (spec AC-08).

    The file is read again here rather than trusting a token from the preview
    call — the operator may have picked a different file in between, and
    re-reading is nothing next to importing a library nobody looked at.
    """
    await _assert_can_write(login_user)

    raw = await file.read()
    preview = await term_importer.import_terms(
        raw, file.filename or '', mode=mode, user_id=login_user.user_id
    )
    return resp_200({'mode': mode.value, **_preview_dict(preview)})


def _preview_dict(preview: term_importer.TermImportPreview) -> dict:
    """The four counts the confirmation dialog needs (spec AC-07 / AC-08).

    `removed` is what makes the overwrite warning concrete: "this will replace
    everything" is a warning the operator can dismiss, "this will remove 213
    terms" is one they read.
    """
    return {
        'added': preview.added,
        'duplicates': preview.duplicates,
        'dropped_overlong': preview.dropped_overlong,
        'removed': preview.removed,
    }


@router.post('')
async def create_term(
    payload: TermCreatePayload,
    login_user: UserPayload = Depends(UserPayload.get_login_user),
):
    """Add one term (spec AC-01 ~ AC-04, AC-06)."""
    await _assert_can_write(login_user)

    term = await term_service.create_term(
        user_id=login_user.user_id,
        standard_term=payload.standard_term,
        definition=payload.definition,
        aliases=payload.aliases,
        department_id=payload.department_id,
    )
    return resp_200(_term_dict(term))


@router.put('/{term_id:int}')
async def update_term(
    term_id: int,
    payload: TermUpdatePayload,
    login_user: UserPayload = Depends(UserPayload.get_login_user),
):
    """Replace one term's editable fields (spec AC-01 ~ AC-04)."""
    await _assert_can_write(login_user)

    term = await term_service.update_term(
        term_id=term_id,
        standard_term=payload.standard_term,
        definition=payload.definition,
        aliases=payload.aliases,
        department_id=payload.department_id,
    )
    return resp_200(_term_dict(term))


@router.delete('/{term_id:int}')
async def delete_term(
    term_id: int,
    login_user: UserPayload = Depends(UserPayload.get_login_user),
):
    """Delete one term (spec AC-01).

    No cascade and no tombstone: the term block was never persisted (contract
    INV-18), so past answers keep rendering exactly as they were and the only
    thing that changes is what the next turn is told.
    """
    await _assert_can_write(login_user)

    await term_service.delete_term(term_id)
    return resp_200({'deleted': True})
