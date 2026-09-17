"""Template CRUD endpoints.

Gated by `get_tenant_admin_user`, which admits the platform super admin and the
current tenant's admin (its first branch is a global-super check). Templates are
tenant-level configuration rather than OpenFGA-governed resources, so no
authorize() call is involved -- see design decision 8.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from bisheng.common.dependencies.user_deps import UserPayload
from bisheng.common.schemas.api import resp_200
from bisheng.doc_extract.domain.schemas import DocExtractTemplatePayload, DocExtractEnabledPayload

router = APIRouter(prefix='/doc_extract/templates', tags=['doc-extract'])


def _dump(template) -> dict:
    return {
        'id': template.id,
        'name': template.name,
        'doc_type': template.doc_type,
        'description': template.description,
        'field_schema': template.field_schema or [],
        'render_template': template.render_template or '',
        'enabled': template.enabled,
        'create_user': template.create_user,
        'create_time': template.create_time,
        'update_time': template.update_time,
    }


@router.get('')
async def list_templates(
    enabled_only: bool = False,
    login_user: UserPayload = Depends(UserPayload.get_tenant_admin_user),
):
    from bisheng.doc_extract.domain.services.template_service import TemplateService

    templates = await TemplateService.alist(enabled_only=enabled_only)
    return resp_200([_dump(t) for t in templates])


@router.get('/{template_id}')
async def get_template(
    template_id: int,
    login_user: UserPayload = Depends(UserPayload.get_tenant_admin_user),
):
    from bisheng.doc_extract.domain.services.template_service import TemplateService

    return resp_200(_dump(await TemplateService.aget(template_id)))


@router.post('')
async def create_template(
    payload: DocExtractTemplatePayload,
    login_user: UserPayload = Depends(UserPayload.get_tenant_admin_user),
):
    from bisheng.doc_extract.domain.services.template_service import TemplateService

    template = await TemplateService.acreate(
        operator_id=login_user.user_id,
        name=payload.name,
        doc_type=payload.doc_type,
        description=payload.description,
        field_schema=payload.field_schema,
        render_template=payload.render_template,
    )
    return resp_200(_dump(template))


@router.put('/{template_id}')
async def update_template(
    template_id: int,
    payload: DocExtractTemplatePayload,
    login_user: UserPayload = Depends(UserPayload.get_tenant_admin_user),
):
    from bisheng.doc_extract.domain.services.template_service import TemplateService

    template = await TemplateService.aupdate(
        operator_id=login_user.user_id,
        template_id=template_id,
        name=payload.name,
        doc_type=payload.doc_type,
        description=payload.description,
        field_schema=payload.field_schema,
        render_template=payload.render_template,
    )
    return resp_200(_dump(template))


@router.put('/{template_id}/enabled')
async def set_template_enabled(
    template_id: int,
    payload: DocExtractEnabledPayload,
    login_user: UserPayload = Depends(UserPayload.get_tenant_admin_user),
):
    """Enable / disable. A separate sub-resource rather than a field on the full
    update, so toggling does not require resending the whole definition."""
    from bisheng.doc_extract.domain.services.template_service import TemplateService

    template = await TemplateService.aset_enabled(
        operator_id=login_user.user_id,
        template_id=template_id,
        enabled=payload.enabled,
    )
    return resp_200(_dump(template))


@router.delete('/{template_id}')
async def delete_template(
    template_id: int,
    login_user: UserPayload = Depends(UserPayload.get_tenant_admin_user),
):
    from bisheng.doc_extract.domain.services.template_service import TemplateService

    await TemplateService.adelete(operator_id=login_user.user_id, template_id=template_id)
    return resp_200({})
