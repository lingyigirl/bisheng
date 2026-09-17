from __future__ import annotations

import time
from datetime import datetime
from typing import TYPE_CHECKING

from loguru import logger

from bisheng.chat_session.domain.models.feedback_event import (
    BACKFILL_WINDOW_MINUTES,
    MessageFeedbackEventDao,
)

if TYPE_CHECKING:
    from bisheng.chat_session.domain.models.feedback_event import MessageFeedbackEvent

MAX_COMMENT_LENGTH = 4096
"""Matches the `message_feedback_event.comment` column width."""

BACKFILL_ATTEMPTS = 3
BACKFILL_RETRY_DELAY_SECONDS = 0.2
"""Bounds for the comment backfill retry.

The verdict and the reason are two racing requests from the frontend, so the
first attempt can legitimately miss on a fresh dislike. 3 attempts over ~0.4s
covers the in-flight window without holding a worker for long; a genuine miss
(the user submitted no reason, or the row is out of window) still costs only
0.4s on a best-effort path.
"""


class FeedbackEventService:
    """Append-only audit trail for message ratings (spec AC-21..AC-27).

    Every write here is BEST-EFFORT. `chatmessage.liked` is the source of truth
    for the current verdict and is written first by the caller; this trail is the
    history beside it. An audit failure must therefore never propagate — see
    `record` and `backfill_comment`.
    """

    @classmethod
    def record(
        cls,
        *,
        tenant_id: int,
        user_id: int | None,
        message_id: int,
        flow_id: str | None,
        from_value: int,
        to_value: int,
    ) -> None:
        """Append one rating event. Never raises.

        An unchanged verdict writes nothing: it is not a user action, and letting
        it through would bloat the trail with rows that all say the same thing
        (spec AC-27). `like_response` already returns early in that case; this
        guard exists so a future caller that forgets cannot corrupt the counts.

        Failures are swallowed with a warning. The verdict has already been
        persisted and the session counters updated by the time this runs, so
        raising would report a 500 for an action that in fact succeeded.
        """
        if from_value == to_value:
            return

        try:
            MessageFeedbackEventDao.create(
                tenant_id=tenant_id,
                user_id=user_id,
                message_id=message_id,
                flow_id=flow_id,
                from_value=from_value,
                to_value=to_value,
            )
        except Exception:
            # Deliberately broad: this is a non-critical side record, and any
            # failure mode (DB down, schema drift, constraint) must degrade to a
            # warning rather than break the rating the user just made.
            logger.opt(exception=True).warning(
                'feedback event write failed message_id={} from={} to={}',
                message_id,
                from_value,
                to_value,
            )

    @classmethod
    def backfill_comment(
        cls,
        *,
        message_id: int,
        user_id: int | None,
        comment: str,
        within_minutes: int = BACKFILL_WINDOW_MINUTES,
    ) -> bool:
        """Attach a dislike reason to the event it belongs to.

        The frontend defers dislike: clicking thumbs-down only opens the reason
        dialog, and nothing is persisted until submit — which means the verdict
        (`POST /liked`) and the reason (`POST /chat/comment`) arrive as two
        separate requests. They are issued without awaiting one another, so the
        reason can be served BEFORE the event row exists; the retry below covers
        that window. This fills the reason into the row the verdict request
        created.

        A blank comment is skipped rather than stored: the dialog allows submit
        with no reason, and an empty string would make the row look backfilled and
        block a genuine reason from landing on it later.

        A miss is not an error (design 决策 6) — the reason is already on
        `chatmessage.remark`, and inventing a standalone event would double-count
        the rating.
        """
        cleaned = (comment or '').strip()
        if not cleaned:
            return False

        # The frontend submits a dislike as TWO requests in the same tick — the
        # verdict (`/liked`) and the reason (`/chat/comment`) — without awaiting
        # the first (`MessageFeedbackButtons.handleSubmitComment`). They race, so
        # on a fresh dislike the reason request can be served before the event row
        # exists. Retry briefly rather than dropping the reason: the window is
        # tens of milliseconds and the alternative (a silent miss) loses exactly
        # the case the feature exists for.
        #
        # Deliberately a bounded, blocking retry: this runs on a threadpool
        # endpoint, the table is tiny, and the write is best-effort — never
        # raising is the contract (design 决策 6).
        try:
            for attempt in range(BACKFILL_ATTEMPTS):
                filled = MessageFeedbackEventDao.backfill_comment(
                    message_id=message_id,
                    user_id=user_id,
                    comment=cleaned[:MAX_COMMENT_LENGTH],
                    within_minutes=within_minutes,
                )
                if filled:
                    return True
                if attempt < BACKFILL_ATTEMPTS - 1:
                    time.sleep(BACKFILL_RETRY_DELAY_SECONDS)
            return False
        except Exception:
            logger.opt(exception=True).warning(
                'feedback comment backfill failed message_id={}', message_id
            )
            return False

    @classmethod
    async def alist(
        cls,
        *,
        tenant_id: int | None,
        flow_id: str | None = None,
        feedback_type: int | None = None,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[list[MessageFeedbackEvent], int]:
        """Page through the trail.

        `tenant_id=None` means "no tenant narrowing", which is only correct on the
        super-admin path where the caller runs inside `bypass_tenant_filter()`.
        Passing an explicit id states the narrowing instead of relying on the
        automatic filter, because on the super-admin path that filter is off in
        the same process.
        """
        return await MessageFeedbackEventDao.alist(
            tenant_id=tenant_id,
            flow_id=flow_id,
            feedback_type=feedback_type,
            start_time=start_time,
            end_time=end_time,
            page=page,
            page_size=page_size,
        )
