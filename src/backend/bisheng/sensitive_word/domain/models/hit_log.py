from __future__ import annotations

from datetime import datetime
from typing import Any

from loguru import logger
from pydantic import ValidationError
from sqlalchemy import Column, DateTime, Integer, String, text
from sqlalchemy.exc import SQLAlchemyError
from sqlmodel import Field

from bisheng.common.models.base import SQLModelSerializable
from bisheng.core.database import get_async_db_session
from bisheng.core.database.dialect_helpers import JsonType


class SensitiveWordHitLog(SQLModelSerializable, table=True):
    """Append-only audit log for chat guardrail hits (spec AC-07/AC-13).

    Stores only hit summaries (word + count), never the raw conversation text.
    """

    __tablename__ = "sensitive_word_hit_log"
    __table_args__ = ({"mysql_charset": "utf8mb4", "mysql_collate": "utf8mb4_unicode_ci"},)

    id: int | None = Field(default=None, primary_key=True)
    tenant_id: int = Field(
        sa_column=Column(Integer, nullable=False, index=True, comment="租户ID"),
    )
    business_type: str = Field(
        sa_column=Column(String(64), nullable=False, index=True, comment="业务场景"),
    )
    direction: str = Field(
        sa_column=Column(String(16), nullable=False, comment="检测方向: input/output"),
    )
    flow_id: str | None = Field(
        default=None,
        sa_column=Column(String(64), nullable=True, comment="应用ID"),
    )
    chat_id: str | None = Field(
        default=None,
        sa_column=Column(String(64), nullable=True, comment="会话ID"),
    )
    user_id: int | None = Field(
        default=None,
        sa_column=Column(Integer, nullable=True, comment="用户ID"),
    )
    hits: list[dict[str, Any]] = Field(
        default_factory=list,
        sa_column=Column(JsonType, nullable=True, comment='命中词摘要 [{"word","count"}], 不存原文'),
    )
    action: str = Field(
        sa_column=Column(String(16), nullable=False, comment="命中动作: block/mask"),
    )
    source: str | None = Field(
        default=None,
        sa_column=Column(
            String(16),
            nullable=True,
            server_default=text("'tenant'"),
            comment="命中来源: tenant=租户策略, platform=平台强制词库; NULL 等同 tenant",
        ),
    )
    create_time: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime, nullable=False, server_default=text("CURRENT_TIMESTAMP"), comment="创建时间"),
    )


class SensitiveWordHitLogDao:
    @classmethod
    async def alist(
        cls,
        *,
        tenant_id: int | None,
        direction: str | None = None,
        flow_id: str | None = None,
        source: str | None = None,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[list[SensitiveWordHitLog], int]:
        """Page through hit logs (newest first) with optional filters.

        `tenant_id=None` means "no tenant narrowing", used only by the
        super-admin query, which runs with the automatic tenant filter bypassed.
        Stating the tenant explicitly when one IS given matters there: with the
        filter off in that process, relying on it would silently return every
        tenant's rows.
        """
        from sqlalchemy import func, select

        where_clauses = []
        if tenant_id is not None:
            where_clauses.append(SensitiveWordHitLog.tenant_id == tenant_id)
        if direction:
            where_clauses.append(SensitiveWordHitLog.direction == direction)
        if flow_id:
            where_clauses.append(SensitiveWordHitLog.flow_id == flow_id)
        if source:
            where_clauses.append(SensitiveWordHitLog.source == source)
        if start_time:
            where_clauses.append(SensitiveWordHitLog.create_time >= start_time)
        if end_time:
            where_clauses.append(SensitiveWordHitLog.create_time <= end_time)

        async with get_async_db_session() as session:
            total = await session.scalar(
                select(func.count()).select_from(SensitiveWordHitLog).where(*where_clauses)
            )
            rows = await session.exec(
                select(SensitiveWordHitLog)
                .where(*where_clauses)
                .order_by(SensitiveWordHitLog.id.desc())
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
            # 租户自动过滤器的 do_orm_execute 会把 SELECT 包装成 Row 元组
            # (字段名为实体名), 需解包 row[0] 取回实体
            return [row[0] for row in rows.all()], int(total or 0)

    @classmethod
    async def acreate_async_best_effort(
        cls,
        *,
        tenant_id: int,
        business_type: str,
        direction: str,
        action: str,
        hits: list[dict[str, Any]],
        flow_id: str | None = None,
        chat_id: str | None = None,
        user_id: int | None = None,
        source: str = "tenant",
    ) -> None:
        """Write one hit record without ever breaking the chat main flow.

        Best-effort audit data (design 决策 5): blocking/detection already happened
        before this call, so a failed log write must not fail the conversation
        (spec AC-16) — warn with traceback instead of raising.

        `source` defaults to `tenant` so every F056 call site keeps its meaning
        unchanged (spec AC-19); F059's platform layer passes `platform`.
        """
        try:
            async with get_async_db_session() as session:
                session.add(
                    SensitiveWordHitLog(
                        tenant_id=tenant_id,
                        business_type=business_type,
                        direction=direction,
                        action=action,
                        hits=hits,
                        flow_id=flow_id,
                        chat_id=chat_id,
                        user_id=user_id,
                        source=source,
                    )
                )
                await session.commit()
        except (SQLAlchemyError, ValidationError):
            logger.opt(exception=True).warning(
                "sensitive word hit log write failed for tenant={} direction={}",
                tenant_id,
                direction,
            )
