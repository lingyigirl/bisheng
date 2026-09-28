"""DAO-level coverage for the F059 feedback trail.

`test_feedback_event_service.py` stubs `MessageFeedbackEventDao` outright, so
nothing there executes the DAO's SQL. That gap is how the dislike-reason backfill
shipped broken: `backfill_comment` read `session.exec()`'s result as an ORM
entity, but the statement is built with **SQLAlchemy's** `select` (not SQLModel's)
and therefore yields Row tuples — so `row.comment = ...` raised AttributeError
straight into the service's deliberately broad `except Exception`. The reason was
durably written to `chatmessage.remark` and silently dropped from the event row,
which is the only place the platform's 问答评价 tab reads it from.

These tests run the real statement against a real session instead.
"""

from contextlib import contextmanager
from datetime import datetime

import pytest

from bisheng.chat_session.domain.models.feedback_event import (
    MessageFeedbackEvent,
    MessageFeedbackEventDao,
)

USER_ID = 7
MESSAGE_ID = 100


@pytest.fixture()
def feedback_db(db_session):
    """The production table, on the shared in-memory session (rolled back per test)."""
    MessageFeedbackEvent.__table__.create(bind=db_session.connection(), checkfirst=True)
    return db_session


@pytest.fixture()
def dao_db(feedback_db, monkeypatch):
    """Point the DAO's sync session factory at the test session."""

    @contextmanager
    def _session():
        yield feedback_db

    monkeypatch.setattr(
        'bisheng.chat_session.domain.models.feedback_event.get_sync_db_session',
        _session,
    )
    return feedback_db


def _create_event(dao_db, to_value: int) -> int:
    """Insert one event and return its id, stamped in *local* time.

    SQLite's `CURRENT_TIMESTAMP` is UTC while the DAO's freshness window is
    computed with local `datetime.now()`, so the column default would put a
    just-created row ten hours in the past and the window — not the code under
    test — would decide the outcome. Production has no such skew (MySQL runs on
    the app's clock), so pinning it here keeps the assertion non-vacuous.
    """
    row = MessageFeedbackEventDao.create(
        tenant_id=1,
        user_id=USER_ID,
        message_id=MESSAGE_ID,
        flow_id='app-1',
        from_value=0,
        to_value=to_value,
    )
    row.create_time = datetime.now()
    dao_db.add(row)
    dao_db.commit()
    return row.id


def test_backfill_writes_the_reason_onto_the_dislike_row(dao_db, tenant_context):
    """The reason typed under /workspace must land on the dislike event row.

    The platform's 问答评价 tab reads `comment` off this table and nowhere else,
    so a swallowed write here is a permanent "-" in the 差评原因 column.
    """
    tenant_context(1)
    row_id = _create_event(dao_db, to_value=2)

    filled = MessageFeedbackEventDao.backfill_comment(
        message_id=MESSAGE_ID, user_id=USER_ID, comment='答非所问'
    )

    assert filled is True
    # Re-read for real: the identity map would otherwise hand back the same
    # in-memory object and the assertion below would hold vacuously.
    dao_db.expire_all()
    assert dao_db.get(MessageFeedbackEvent, row_id).comment == '答非所问'


def test_backfill_leaves_a_liked_row_alone(dao_db, tenant_context):
    """Only dislikes carry a reason — matching a like must not attach one to it."""
    tenant_context(1)
    row_id = _create_event(dao_db, to_value=1)

    assert (
        MessageFeedbackEventDao.backfill_comment(
            message_id=MESSAGE_ID, user_id=USER_ID, comment='答非所问'
        )
        is False
    )

    dao_db.expire_all()
    assert dao_db.get(MessageFeedbackEvent, row_id).comment is None
