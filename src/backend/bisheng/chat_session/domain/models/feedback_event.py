from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import Column, DateTime, Integer, String, func, select, text
from sqlmodel import Field

from bisheng.common.models.base import SQLModelSerializable
from bisheng.core.database import get_async_db_session, get_sync_db_session

# Storage boundary (release-contract INV-11, same as F056's hit log): this table
# records IDENTIFIERS AND VERDICTS ONLY — never the conversation text. The answer
# a user rated is reachable through `message_id`; the free-text `comment` is the
# user's own words about the rating, not the model's answer.
#
# THIS TABLE IS TENANT-SCOPED and MUST stay that way. Its model module has to be
# listed in `_TENANT_AWARE_MODEL_MODULES` (core/database/tenant_filter.py) — the
# tenant-aware table set is discovered ONCE at startup from SQLModel.metadata, so
# a module that nothing imports is invisible to BOTH the read filter and the
# `before_flush` tenant-id fill. The failure is silent and severe: tenants would
# read each other's feedback and rows would be written with a NULL tenant_id.
# (v2.5 shipped exactly this bug for knowledgefile / flowversion / roleaccess.)

BACKFILL_WINDOW_MINUTES = 10
"""How far back `abackfill_comment` looks for the dislike event a comment belongs to.

The frontend submits a dislike in two requests (deferred-reason dialog): POST
/liked sets liked=2, then POST /chat/comment carries the reason. The window only
has to cover a user reading the dialog and typing — 10 minutes is generous, and
keeping it tight is what stops the comment from landing on an older, unrelated
dislike of the same message by the same user.
"""


class MessageFeedbackEvent(SQLModelSerializable, table=True):
    """Append-only log of every rating action (spec AC-21..AC-27).

    `chatmessage.liked` holds only the CURRENT verdict — a user who likes,
    cancels, then dislikes leaves a single `liked=1` behind. This table keeps the
    full action trail so the real behaviour is auditable after the fact.
    """

    __tablename__ = "message_feedback_event"
    __table_args__ = ({"mysql_charset": "utf8mb4", "mysql_collate": "utf8mb4_unicode_ci"},)

    id: int | None = Field(default=None, primary_key=True)
    tenant_id: int = Field(
        sa_column=Column(Integer, nullable=False, index=True, comment="租户ID"),
    )
    user_id: int | None = Field(default=None, sa_column=Column(Integer, nullable=True, comment="操作人ID"))
    message_id: int = Field(
        sa_column=Column(Integer, nullable=False, index=True, comment="被评价的消息ID"),
    )
    flow_id: str | None = Field(
        default=None,
        sa_column=Column(String(64), nullable=True, comment="所属应用ID"),
    )
    from_value: int = Field(
        sa_column=Column(Integer, nullable=False, comment="变更前评价: 0未评/1点赞/2差评"),
    )
    to_value: int = Field(
        sa_column=Column(Integer, nullable=False, comment="变更后评价: 0未评/1点赞/2差评"),
    )
    comment: str | None = Field(
        default=None,
        sa_column=Column(String(4096), nullable=True, comment="差评原因(用户自填, 后到回填)"),
    )
    create_time: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime, nullable=False, server_default=text("CURRENT_TIMESTAMP"), comment="创建时间"),
    )


class MessageFeedbackEventDao:
    """Write methods are SYNC, read methods are ASYNC — deliberately.

    The write path hangs off `ChatMessageService.like_response` /
    `comment_on_answer`, which are synchronous and reached from synchronous
    FastAPI endpoints (run in a threadpool). Driving the async engine from there
    would spin up a second event loop per request and contend with the pool bound
    to the main loop, so writes use `get_sync_db_session()` — the same idiom the
    neighbouring `comment_answer` already uses. The query path is only ever
    called from an async endpoint, hence async.
    """

    @classmethod
    def create(
        cls,
        *,
        tenant_id: int,
        user_id: int | None,
        message_id: int,
        flow_id: str | None,
        from_value: int,
        to_value: int,
    ) -> MessageFeedbackEvent:
        with get_sync_db_session() as session:
            row = MessageFeedbackEvent(
                tenant_id=tenant_id,
                user_id=user_id,
                message_id=message_id,
                flow_id=flow_id,
                from_value=from_value,
                to_value=to_value,
            )
            session.add(row)
            session.commit()
            session.refresh(row)
            return row

    @classmethod
    def backfill_comment(
        cls,
        *,
        message_id: int,
        user_id: int | None,
        comment: str,
        within_minutes: int = BACKFILL_WINDOW_MINUTES,
    ) -> bool:
        """Attach a dislike reason to the event it belongs to. Returns True when filled.

        Only ever touches a row that (a) is a *dislike* (`to_value = 2`), (b) is
        still comment-less, and (c) falls inside the window — the newest such row
        for this message and user. Matching on message_id alone would let the
        reason land on a days-old dislike that happened to have no reason; the
        user_id narrowing matters because the same message can be rated by more
        than one person in a shared conversation.

        A miss is NOT an error and must not raise: the reason is already stored on
        `chatmessage.remark`, so nothing is lost, and inventing a standalone event
        would double-count the rating (design 决策 6).
        """
        cutoff = datetime.now() - timedelta(minutes=within_minutes)
        with get_sync_db_session() as session:
            stmt = (
                select(MessageFeedbackEvent)
                .where(
                    MessageFeedbackEvent.message_id == message_id,
                    MessageFeedbackEvent.to_value == 2,
                    MessageFeedbackEvent.comment.is_(None),
                    MessageFeedbackEvent.create_time >= cutoff,
                )
                .order_by(MessageFeedbackEvent.id.desc())
                .limit(1)
            )
            if user_id is not None:
                stmt = stmt.where(MessageFeedbackEvent.user_id == user_id)
            row = session.exec(stmt).first()
            if row is None:
                return False
            row.comment = (comment or "")[:4096]
            session.add(row)
            session.commit()
            return True

    @classmethod
    async def alist(
        cls,
        *,
        tenant_id: int | None = None,
        flow_id: str | None = None,
        feedback_type: int | None = None,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[list[MessageFeedbackEvent], int]:
        """Page through feedback events (newest first).

        `tenant_id=None` means "no tenant narrowing" — used by the super-admin
        query, which runs inside `bypass_tenant_filter()`. When a tenant id is
        given it is stated explicitly rather than relying on the automatic filter,
        because the super-admin path runs with that filter disabled in the same
        process.
        """
        where_clauses = []
        if tenant_id is not None:
            where_clauses.append(MessageFeedbackEvent.tenant_id == tenant_id)
        if flow_id:
            where_clauses.append(MessageFeedbackEvent.flow_id == flow_id)
        if feedback_type is not None:
            where_clauses.append(MessageFeedbackEvent.to_value == feedback_type)
        if start_time:
            where_clauses.append(MessageFeedbackEvent.create_time >= start_time)
        if end_time:
            where_clauses.append(MessageFeedbackEvent.create_time <= end_time)

        async with get_async_db_session() as session:
            total = await session.scalar(
                select(func.count()).select_from(MessageFeedbackEvent).where(*where_clauses)
            )
            rows = await session.exec(
                select(MessageFeedbackEvent)
                .where(*where_clauses)
                .order_by(MessageFeedbackEvent.id.desc())
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
            # The tenant auto-filter wraps SELECTs into Row tuples (column named
            # after the entity); unwrap row[0] to get the entity back. Same reason
            # as sensitive_word/domain/models/hit_log.py.
            return [row[0] for row in rows.all()], int(total or 0)
