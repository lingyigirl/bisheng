from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import Column, DateTime, Integer, SmallInteger, String, Text, text
from sqlmodel import Field, select

from bisheng.common.models.base import SQLModelSerializable
from bisheng.core.context.tenant import get_current_tenant_id, strict_tenant_filter
from bisheng.core.database import get_async_db_session, get_sync_db_session
from bisheng.core.database.dialect_helpers import JsonType, LargeText

ROOT_TENANT_ID = 1


def _belongs_to_current_tenant(template) -> bool:
    """Whether ``template`` belongs to the tenant making this call.

    Belt-and-braces on top of the narrowed SELECT: a mutated ORM row is flushed
    without any tenant predicate (the listener only rewrites SELECTs), so the
    ownership check is what actually prevents a write crossing tenants if the
    read ever widens again.

    No current tenant in context means an internal/system call -- allowed, since
    the narrowed read has already restricted what it could have loaded.
    """
    current = get_current_tenant_id()
    if current is None:
        return True
    return template.tenant_id == current


class DocExtractTemplateBase(SQLModelSerializable):
    """A per-document-type extraction template.

    ``doc_type`` is indexed but deliberately NOT unique at the DB level: deletes
    are soft (``is_deleted``), so a unique constraint would let a deleted row
    block re-creating a template with the same identifier -- which the spec
    requires to be possible. Uniqueness is enforced in the service layer against
    live rows only.
    """

    tenant_id: int = Field(
        sa_column=Column(Integer, nullable=False, index=True, comment='租户ID'),
    )
    name: str = Field(
        sa_column=Column(String(128), nullable=False, comment='模板名称'),
    )
    doc_type: str = Field(
        sa_column=Column(String(64), nullable=False, index=True, comment='文档类型标识'),
    )
    description: str = Field(
        sa_column=Column(Text, nullable=False, comment='适用范围：分类时喂给模型的该类描述'),
    )
    field_schema: list[dict[str, Any]] = Field(
        default_factory=list,
        sa_column=Column(JsonType, nullable=True, comment='字段定义数组'),
    )
    render_template: str = Field(
        default='',
        sa_column=Column(LargeText, nullable=False, comment='呈现形式，可为空'),
    )
    enabled: int = Field(
        default=1,
        sa_column=Column(SmallInteger, nullable=False, server_default=text('1'), comment='是否启用'),
    )
    is_deleted: int = Field(
        default=0,
        sa_column=Column(SmallInteger, nullable=False, server_default=text('0'), comment='软删标记'),
    )
    create_user: int | None = Field(
        default=None, sa_column=Column(Integer, nullable=True, comment='最后修改人ID'),
    )
    create_time: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime, nullable=False, server_default=text('CURRENT_TIMESTAMP')),
    )
    update_time: datetime | None = Field(
        default=None,
        sa_column=Column(
            DateTime,
            nullable=False,
            server_default=text('CURRENT_TIMESTAMP'),
            onupdate=text('CURRENT_TIMESTAMP'),
        ),
    )


class DocExtractTemplate(DocExtractTemplateBase, table=True):
    __tablename__ = 'doc_extract_template'
    __table_args__ = (
        {'mysql_charset': 'utf8mb4', 'mysql_collate': 'utf8mb4_unicode_ci'},
    )

    id: int | None = Field(default=None, primary_key=True)


class DocExtractTemplateDao:
    """DAO for ``doc_extract_template``.

    **Every read runs inside ``strict_tenant_filter()``.** Templates are
    tenant-level configuration: a tenant must never see or touch another
    tenant's, Root's included. The global listener's default for a Child tenant
    is the IN-list ``WHERE tenant_id IN (child, ROOT)`` -- which is right for
    *shared* resources but wrong here, and it only filters SELECTs.

    That combination is the trap this module has to avoid: a SELECT would return
    a Root row, and the subsequent dirty-flush UPDATE is **not** rewritten by
    the listener (``tenant_filter.py``: `if not orm_execute_state.is_select:
    return`), nor by ``before_flush`` (which only fills ``session.new``). So
    without the narrowing, a Child Admin could update or soft-delete a Root
    template simply by passing its id -- and ids are small integers.

    Same reasoning and same idiom as ``linsight/domain/models/linsight_skill.py``
    (see its class docstring) and ``knowledge_department_service.py``.
    """

    @classmethod
    def _live_stmt(cls):
        return select(DocExtractTemplate).where(DocExtractTemplate.is_deleted == 0)

    @classmethod
    def get_by_id(cls, template_id: int) -> DocExtractTemplate | None:
        with strict_tenant_filter(), get_sync_db_session() as session:
            return session.exec(cls._live_stmt().where(DocExtractTemplate.id == template_id)).first()

    @classmethod
    async def aget_by_id(cls, template_id: int) -> DocExtractTemplate | None:
        with strict_tenant_filter():
            async with get_async_db_session() as session:
                result = await session.exec(
                    cls._live_stmt().where(DocExtractTemplate.id == template_id)
                )
                return result.first()

    @classmethod
    def get_by_doc_type(cls, doc_type: str) -> DocExtractTemplate | None:
        """Live template for ``doc_type``, or None. Deleted rows are excluded so
        the identifier is free for reuse."""
        with strict_tenant_filter(), get_sync_db_session() as session:
            return session.exec(cls._live_stmt().where(DocExtractTemplate.doc_type == doc_type)).first()

    @classmethod
    async def aget_by_doc_type(cls, doc_type: str) -> DocExtractTemplate | None:
        with strict_tenant_filter():
            async with get_async_db_session() as session:
                result = await session.exec(
                    cls._live_stmt().where(DocExtractTemplate.doc_type == doc_type)
                )
                return result.first()

    @classmethod
    def list_templates(cls, enabled_only: bool = False) -> list[DocExtractTemplate]:
        stmt = cls._live_stmt()
        if enabled_only:
            stmt = stmt.where(DocExtractTemplate.enabled == 1)
        with strict_tenant_filter(), get_sync_db_session() as session:
            return list(session.exec(stmt).all())

    @classmethod
    async def alist_templates(cls, enabled_only: bool = False) -> list[DocExtractTemplate]:
        stmt = cls._live_stmt()
        if enabled_only:
            stmt = stmt.where(DocExtractTemplate.enabled == 1)
        with strict_tenant_filter():
            async with get_async_db_session() as session:
                result = await session.exec(stmt)
                return list(result.all())

    @classmethod
    async def acreate(cls, data: dict[str, Any]) -> DocExtractTemplate:
        template = DocExtractTemplate(**data)
        async with get_async_db_session() as session:
            session.add(template)
            await session.commit()
            await session.refresh(template)
            return template

    @classmethod
    async def aupdate(cls, template_id: int, data: dict[str, Any]) -> DocExtractTemplate | None:
        """Update a template that belongs to the current tenant.

        The lookup is narrowed (see the class docstring), and the ownership is
        re-asserted before the write: the narrowed SELECT is what makes the row
        unreachable, but a mutated row is persisted by the flush with no tenant
        predicate at all, so the check is the thing that actually holds if a
        future filter change re-widens the read.
        """
        with strict_tenant_filter():
            async with get_async_db_session() as session:
                result = await session.exec(
                    cls._live_stmt().where(DocExtractTemplate.id == template_id)
                )
                template = result.first()
                if template is None or not _belongs_to_current_tenant(template):
                    return None
                for key, value in data.items():
                    setattr(template, key, value)
                session.add(template)
                await session.commit()
                await session.refresh(template)
                return template

    @classmethod
    async def asoft_delete(cls, template_id: int) -> bool:
        with strict_tenant_filter():
            async with get_async_db_session() as session:
                result = await session.exec(
                    cls._live_stmt().where(DocExtractTemplate.id == template_id)
                )
                template = result.first()
                if template is None or not _belongs_to_current_tenant(template):
                    return False
                template.is_deleted = 1
                session.add(template)
            await session.commit()
            return True
