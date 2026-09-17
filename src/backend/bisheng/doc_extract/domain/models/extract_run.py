from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import Column, DateTime, Float, Integer, SmallInteger, String, text
from sqlmodel import Field, select

from bisheng.common.models.base import SQLModelSerializable
from bisheng.core.context.tenant import strict_tenant_filter
from bisheng.core.database import get_async_db_session
from bisheng.core.database.dialect_helpers import JsonType, LargeText


class DocExtractRunBase(SQLModelSerializable):
    """One extraction run, for traceability.

    Only written once extraction has actually started: requests rejected by the
    precheck table leave no row (there is no template to attribute them to yet),
    and trial runs (spec M-1b) never write at all. See design decision 11.
    """

    tenant_id: int = Field(
        sa_column=Column(Integer, nullable=False, index=True, comment='租户ID'),
    )
    template_id: int = Field(
        sa_column=Column(Integer, nullable=False, index=True, comment='所用模板ID（可能是软删行）'),
    )
    doc_type: str = Field(
        sa_column=Column(String(64), nullable=False, comment='文档类型标识；冗余，模板改名/删除后仍可追溯'),
    )
    determined_by: str = Field(
        sa_column=Column(String(16), nullable=False, comment='类型确定方式：explicit=显式指定 / auto=自动判定'),
    )
    confidence: float | None = Field(
        default=None, sa_column=Column(Float, nullable=True, comment='判定置信度；explicit 时为 NULL'),
    )
    preprocess_route: str | None = Field(
        default=None, sa_column=Column(String(16), nullable=True, comment='预处理链路：text / ocr'),
    )
    fields: dict[str, Any] = Field(
        default_factory=dict,
        sa_column=Column(JsonType, nullable=True, comment='抽取出的结构化字段'),
    )
    markdown: str = Field(
        default='',
        sa_column=Column(LargeText, nullable=False, comment='渲染结果'),
    )
    status: int = Field(
        default=1,
        sa_column=Column(SmallInteger, nullable=False, server_default=text('1'), comment='1=成功 0=失败'),
    )
    error_code: int | None = Field(
        default=None, sa_column=Column(Integer, nullable=True, comment='失败错误码'),
    )
    error_message: str | None = Field(
        default=None, sa_column=Column(String(500), nullable=True, comment='失败原因'),
    )
    user_id: int | None = Field(
        default=None, sa_column=Column(Integer, nullable=True, comment='发起人ID'),
    )
    create_time: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime, nullable=False, server_default=text('CURRENT_TIMESTAMP')),
    )


class DocExtractRun(DocExtractRunBase, table=True):
    __tablename__ = 'doc_extract_run'
    __table_args__ = (
        {'mysql_charset': 'utf8mb4', 'mysql_collate': 'utf8mb4_unicode_ci'},
    )

    id: int | None = Field(default=None, primary_key=True)


class DocExtractRunDao:
    @classmethod
    async def acreate(cls, data: dict[str, Any]) -> DocExtractRun:
        run = DocExtractRun(**data)
        async with get_async_db_session() as session:
            session.add(run)
            await session.commit()
            await session.refresh(run)
            return run

    @classmethod
    async def alist_runs(
        cls,
        template_id: int | None = None,
        status: int | None = None,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
        cursor: int | None = None,
        limit: int = 20,
    ) -> tuple[list[DocExtractRun], int | None]:
        """Newest-first page. ``cursor`` is the id to start *after* (exclusive),
        matching the platform's cursor-pagination convention.

        Returns ``(rows, next_cursor)``; ``next_cursor`` is None on the last page.

        ``limit`` is clamped to at least 1: the pagination below takes
        ``rows[-1]`` to build the cursor, so a caller-supplied 0 or negative
        would index an empty slice. Clamping rather than raising keeps a
        nonsensical page size from turning into a 500 on an admin-only read.
        """
        limit = max(1, limit)

        stmt = select(DocExtractRun)
        if template_id is not None:
            stmt = stmt.where(DocExtractRun.template_id == template_id)
        if status is not None:
            stmt = stmt.where(DocExtractRun.status == status)
        if start_time is not None:
            stmt = stmt.where(DocExtractRun.create_time >= start_time)
        if end_time is not None:
            stmt = stmt.where(DocExtractRun.create_time <= end_time)
        if cursor is not None:
            stmt = stmt.where(DocExtractRun.id < cursor)
        # Fetch one extra row to decide whether another page exists.
        stmt = stmt.order_by(DocExtractRun.id.desc()).limit(limit + 1)

        # Narrowed like the template reads: run records belong to the tenant that
        # produced them and are never shared upward from Root.
        with strict_tenant_filter():
            async with get_async_db_session() as session:
                result = await session.exec(stmt)
                rows = list(result.all())

        if len(rows) > limit:
            rows = rows[:limit]
            next_cursor = rows[-1].id
        else:
            next_cursor = None
        return rows, next_cursor
