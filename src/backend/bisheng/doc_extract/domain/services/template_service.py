"""Template CRUD for document extraction.

All definition validation happens here, at save time. The spec is explicit that
a dangling render reference must be rejected on save rather than rendering
blanks at run time (AC-09) -- by the time a template is in use, nobody is
watching the output for silent omissions.

Permitted to be called from the API layer with no permission logic of its own:
the endpoints carry the `get_tenant_admin_user` dependency (design decision 8 --
templates are tenant-level configuration, not OpenFGA-governed resources).
"""

from __future__ import annotations

from typing import Any

from bisheng.common.errcode.doc_extract import (
    DocExtractRenderRefInvalidError,
    DocExtractTemplateInvalidError,
    DocExtractTemplateMissingError,
    DocExtractTypeDuplicateError,
)
from bisheng.doc_extract.domain.constants import DocExtractAuditAction
from bisheng.doc_extract.domain.models.extract_template import (
    DocExtractTemplate,
    DocExtractTemplateDao,
)
from bisheng.doc_extract.domain.services.audit import write_template_audit
from bisheng.doc_extract.domain.services.field_validator import FIELD_TYPES
from bisheng.doc_extract.domain.services.renderer import extract_referenced_keys


class TemplateService:
    @staticmethod
    def validate_definition(
        *,
        name: str,
        doc_type: str,
        description: str,
        field_schema: list[dict[str, Any]],
        render_template: str | None,
    ) -> None:
        """Reject an unusable template before it is stored.

        Raises ``DocExtractTemplateInvalidError`` (11603) or
        ``DocExtractRenderRefInvalidError`` (11604).
        """
        if not (name or '').strip():
            raise DocExtractTemplateInvalidError(msg='模板名称不能为空')
        if not (description or '').strip():
            raise DocExtractTemplateInvalidError(msg='适用范围不能为空')
        if not field_schema:
            raise DocExtractTemplateInvalidError(msg='字段定义不能为空')

        seen: set[str] = set()
        for field in field_schema:
            key = (field.get('key') or '').strip()
            if not key:
                raise DocExtractTemplateInvalidError(msg='字段缺少标识')
            if key in seen:
                raise DocExtractTemplateInvalidError(msg=f'字段标识重复：{key}')
            seen.add(key)
            if field.get('type') not in FIELD_TYPES:
                raise DocExtractTemplateInvalidError(
                    msg=f'字段类型不受支持：{field.get("type")}'
                )

        dangling = extract_referenced_keys(render_template) - seen
        if dangling:
            raise DocExtractRenderRefInvalidError(
                msg=f'呈现形式引用了未定义的字段：{"、".join(sorted(dangling))}'
            )

    @staticmethod
    async def _assert_identifier_free(doc_type: str, *, exclude_id: int | None = None) -> None:
        """Uniqueness applies to live rows only (AC-08).

        The DB deliberately has no unique constraint -- soft-deleted rows would
        occupy the identifier forever (design decision 9 / pitfall 11).

        The caller must pass the **normalized** identifier. An earlier version
        checked the raw input while storing the stripped value, so
        ``'  business_license'`` missed the existing row and then landed as
        ``business_license`` -- two live rows with one identifier, quietly
        defeating the uniqueness rule this function exists to enforce.
        """
        existing = await DocExtractTemplateDao.aget_by_doc_type(doc_type)
        if existing is not None and existing.id != exclude_id:
            raise DocExtractTypeDuplicateError()

    @staticmethod
    def _normalize_identifier(doc_type: str) -> str:
        """Collapse the identifier to the form that gets stored.

        Called before the uniqueness check so the two cannot disagree -- the
        whole point is that what is checked is what is written.
        """
        return (doc_type or '').strip()

    @classmethod
    async def acreate(
        cls,
        *,
        operator_id: int,
        name: str,
        doc_type: str,
        description: str,
        field_schema: list[dict[str, Any]],
        render_template: str | None = None,
    ) -> DocExtractTemplate:
        cls.validate_definition(
            name=name,
            doc_type=doc_type,
            description=description,
            field_schema=field_schema,
            render_template=render_template,
        )
        doc_type = cls._normalize_identifier(doc_type)
        await cls._assert_identifier_free(doc_type)

        template = await DocExtractTemplateDao.acreate({
            'name': name.strip(),
            'doc_type': doc_type,
            'description': description.strip(),
            'field_schema': field_schema,
            'render_template': render_template or '',
            # AC-06: new templates are usable immediately; an admin who wants to
            # check a definition first uses the trial extraction instead.
            'enabled': 1,
            'is_deleted': 0,
            'create_user': operator_id,
        })

        await write_template_audit(
            action=DocExtractAuditAction.TEMPLATE_CREATE,
            operator_id=operator_id,
            template_id=template.id,
            template_name=template.name,
            doc_type=template.doc_type,
            tenant_id=template.tenant_id,
        )
        return template

    @classmethod
    async def aupdate(
        cls,
        *,
        operator_id: int,
        template_id: int,
        name: str,
        doc_type: str,
        description: str,
        field_schema: list[dict[str, Any]],
        render_template: str | None = None,
    ) -> DocExtractTemplate:
        existing = await DocExtractTemplateDao.aget_by_id(template_id)
        if existing is None:
            raise DocExtractTemplateMissingError()

        cls.validate_definition(
            name=name,
            doc_type=doc_type,
            description=description,
            field_schema=field_schema,
            render_template=render_template,
        )
        doc_type = cls._normalize_identifier(doc_type)
        await cls._assert_identifier_free(doc_type, exclude_id=template_id)

        updated = await DocExtractTemplateDao.aupdate(template_id, {
            'name': name.strip(),
            'doc_type': doc_type,
            'description': description.strip(),
            'field_schema': field_schema,
            'render_template': render_template or '',
            'create_user': operator_id,
        })
        if updated is None:
            raise DocExtractTemplateMissingError()

        await write_template_audit(
            action=DocExtractAuditAction.TEMPLATE_UPDATE,
            operator_id=operator_id,
            template_id=updated.id,
            template_name=updated.name,
            doc_type=updated.doc_type,
            tenant_id=updated.tenant_id,
        )
        return updated

    @classmethod
    async def aset_enabled(
        cls,
        *,
        operator_id: int,
        template_id: int,
        enabled: bool,
    ) -> DocExtractTemplate:
        """AC-12: disabling removes the template from business extraction only;
        trial extraction stays available so an admin can keep tuning it."""
        existing = await DocExtractTemplateDao.aget_by_id(template_id)
        if existing is None:
            raise DocExtractTemplateMissingError()

        updated = await DocExtractTemplateDao.aupdate(
            template_id, {'enabled': 1 if enabled else 0}
        )
        if updated is None:
            raise DocExtractTemplateMissingError()

        await write_template_audit(
            action=(
                DocExtractAuditAction.TEMPLATE_ENABLE
                if enabled
                else DocExtractAuditAction.TEMPLATE_DISABLE
            ),
            operator_id=operator_id,
            template_id=updated.id,
            template_name=updated.name,
            doc_type=updated.doc_type,
            tenant_id=updated.tenant_id,
        )
        return updated

    @classmethod
    async def adelete(cls, *, operator_id: int, template_id: int) -> None:
        """Soft delete, so existing extraction records keep resolving their
        template and the identifier becomes free for reuse (AC-08)."""
        existing = await DocExtractTemplateDao.aget_by_id(template_id)
        if existing is None:
            raise DocExtractTemplateMissingError()

        deleted = await DocExtractTemplateDao.asoft_delete(template_id)
        if not deleted:
            raise DocExtractTemplateMissingError()

        await write_template_audit(
            action=DocExtractAuditAction.TEMPLATE_DELETE,
            operator_id=operator_id,
            template_id=existing.id,
            template_name=existing.name,
            doc_type=existing.doc_type,
            tenant_id=existing.tenant_id,
        )

    @classmethod
    async def aget(cls, template_id: int) -> DocExtractTemplate:
        template = await DocExtractTemplateDao.aget_by_id(template_id)
        if template is None:
            # AC-05: "does not exist" and "belongs to another tenant" are the
            # same answer on purpose -- distinguishing them leaks existence.
            raise DocExtractTemplateMissingError()
        return template

    @classmethod
    async def alist(cls, enabled_only: bool = False) -> list[DocExtractTemplate]:
        return await DocExtractTemplateDao.alist_templates(enabled_only=enabled_only)
