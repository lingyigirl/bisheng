"""T011 (F059): the feedback audit trail.

The point of this feature is that `chatmessage.liked` only ever holds the CURRENT
verdict, so a user who likes, cancels and then dislikes leaves a single `liked=2`
behind. These tests pin the append-only trail that survives that, and the
two-request dislike flow that makes the reason arrive *after* the verdict.

No database: the DAO is stubbed so the service's decision logic is what is under
test (the SQL itself is covered by the DAO-level tests in Wave 0's e2e pass).
"""

from types import SimpleNamespace

import pytest
from sqlalchemy.exc import SQLAlchemyError

from bisheng.chat_session.domain.models.feedback_event import MessageFeedbackEventDao
from bisheng.chat_session.domain.services.feedback_event_service import FeedbackEventService


class _Recorder:
    """Captures event writes and mimics the DAO's return value."""

    def __init__(self):
        self.writes = []
        self.raise_on_write = None

    def create(self, **kwargs):
        if self.raise_on_write is not None:
            raise self.raise_on_write
        self.writes.append(kwargs)
        return SimpleNamespace(id=len(self.writes), **kwargs)


class _Backfiller:
    def __init__(self, result=True):
        self.calls = []
        self.result = result

    def backfill_comment(self, **kwargs):
        self.calls.append(kwargs)
        return self.result


@pytest.fixture
def recorder(monkeypatch):
    rec = _Recorder()
    monkeypatch.setattr(MessageFeedbackEventDao, 'create', staticmethod(rec.create))
    return rec


@pytest.fixture
def backfiller(monkeypatch):
    bf = _Backfiller()
    monkeypatch.setattr(MessageFeedbackEventDao, 'backfill_comment', staticmethod(bf.backfill_comment))
    return bf


# ---------------------------------------------------------------------------
# The from/to matrix (spec AC-22)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    'from_value, to_value',
    [
        (0, 1),  # unrated -> like
        (1, 0),  # like -> cancel
        (0, 2),  # unrated -> dislike
        (2, 0),  # dislike -> cancel
        (1, 2),  # like -> dislike
        (2, 1),  # dislike -> like
    ],
)
def test_every_transition_is_recorded(recorder, from_value, to_value):
    FeedbackEventService.record(
        tenant_id=1, user_id=7, message_id=100, flow_id='app-1',
        from_value=from_value, to_value=to_value,
    )

    assert len(recorder.writes) == 1
    write = recorder.writes[0]
    assert (write['from_value'], write['to_value']) == (from_value, to_value)
    assert write['message_id'] == 100
    assert write['user_id'] == 7
    assert write['flow_id'] == 'app-1'
    assert write['tenant_id'] == 1


def test_cancelling_is_recorded_not_ignored(recorder):
    """Cancels matter: without them the trail shows a like the user took back.

    A report that counted rows would over-report satisfaction if cancels (to 0)
    were dropped, and would show an unbroken "liked" history that never happened.
    """
    FeedbackEventService.record(
        tenant_id=1, user_id=7, message_id=100, flow_id=None, from_value=1, to_value=0
    )

    assert recorder.writes[0]['to_value'] == 0


def test_full_toggle_sequence_leaves_three_events(recorder):
    """like -> cancel -> dislike leaves three rows (spec §3 边界)."""
    for from_value, to_value in ((0, 1), (1, 0), (0, 2)):
        FeedbackEventService.record(
            tenant_id=1, user_id=7, message_id=100, flow_id=None,
            from_value=from_value, to_value=to_value,
        )

    assert [(w['from_value'], w['to_value']) for w in recorder.writes] == [(0, 1), (1, 0), (0, 2)]


# ---------------------------------------------------------------------------
# No-op verdicts (spec AC-27)
# ---------------------------------------------------------------------------


def test_unchanged_verdict_writes_nothing(recorder):
    """Re-submitting the same value is not an event.

    `like_response` returns early on an unchanged verdict, so this path should not
    even be reached — but the service refuses it too, so a future caller that
    skips the early return cannot flood the trail with duplicates.
    """
    FeedbackEventService.record(
        tenant_id=1, user_id=7, message_id=100, flow_id=None, from_value=1, to_value=1
    )

    assert recorder.writes == []


def test_unrated_to_unrated_writes_nothing(recorder):
    FeedbackEventService.record(
        tenant_id=1, user_id=7, message_id=100, flow_id=None, from_value=0, to_value=0
    )

    assert recorder.writes == []


# ---------------------------------------------------------------------------
# Best-effort: never break the rating itself (spec AC-24)
# ---------------------------------------------------------------------------


def test_a_failed_write_does_not_propagate(recorder):
    """An audit write must never fail the user's like/dislike.

    The verdict is already persisted on `chatmessage` by the time this runs;
    raising here would turn a successful rating into a 500 for the user.
    """
    recorder.raise_on_write = SQLAlchemyError('boom')

    FeedbackEventService.record(
        tenant_id=1, user_id=7, message_id=100, flow_id=None, from_value=0, to_value=1
    )  # must not raise


def test_an_unexpected_error_does_not_propagate(recorder):
    """Wider than SQLAlchemyError on purpose — anything here is non-fatal."""
    recorder.raise_on_write = ValueError('unexpected')

    FeedbackEventService.record(
        tenant_id=1, user_id=7, message_id=100, flow_id=None, from_value=0, to_value=1
    )  # must not raise


# ---------------------------------------------------------------------------
# Comment backfill (spec AC-23)
# ---------------------------------------------------------------------------


def test_backfill_targets_the_dislike_of_this_message_and_user(backfiller):
    filled = FeedbackEventService.backfill_comment(message_id=100, user_id=7, comment='答非所问')

    assert filled is True
    assert backfiller.calls[0]['message_id'] == 100
    assert backfiller.calls[0]['user_id'] == 7
    assert backfiller.calls[0]['comment'] == '答非所问'


def test_backfill_miss_is_not_an_error(backfiller):
    """A missed backfill must stay silent (design 决策 6).

    The reason is already stored on `chatmessage.remark`, so nothing is lost, and
    the user's dislike already succeeded. Raising would fail a request that the
    user experienced as successful.
    """
    backfiller.result = False

    assert FeedbackEventService.backfill_comment(message_id=100, user_id=7, comment='x') is False


def test_backfill_swallows_dao_errors(monkeypatch):
    def _boom(**kwargs):
        raise SQLAlchemyError('boom')

    monkeypatch.setattr(MessageFeedbackEventDao, 'backfill_comment', staticmethod(_boom))

    assert FeedbackEventService.backfill_comment(message_id=100, user_id=7, comment='x') is False


def test_blank_comment_is_not_written(backfiller):
    """The dislike dialog allows submitting with no reason; that is not a comment.

    Storing an empty string would make the row look "already backfilled" and block
    a later real reason from landing on it.
    """
    FeedbackEventService.backfill_comment(message_id=100, user_id=7, comment='   ')

    assert backfiller.calls == []


def test_backfill_trims_before_storing(backfiller):
    FeedbackEventService.backfill_comment(message_id=100, user_id=7, comment='  答非所问  ')

    assert backfiller.calls[0]['comment'] == '答非所问'


def test_backfill_retries_when_the_verdict_row_has_not_landed_yet(monkeypatch):
    """The verdict and the reason are two RACING requests from the frontend.

    `MessageFeedbackButtons.handleSubmitComment` fires `persist(2)` and
    `onDislikeComment(comment)` in the same tick without awaiting the first, so on
    a fresh dislike the reason can reach the server before the event row exists.
    Without a retry the reason is silently dropped — losing exactly the case the
    feature was built for.
    """
    calls = []

    def _miss_then_hit(**kwargs):
        calls.append(kwargs)
        return len(calls) >= 2  # first attempt: row not there yet

    monkeypatch.setattr(
        MessageFeedbackEventDao, 'backfill_comment', staticmethod(_miss_then_hit)
    )
    monkeypatch.setattr(
        'bisheng.chat_session.domain.services.feedback_event_service.time.sleep',
        lambda _seconds: None,
    )

    assert FeedbackEventService.backfill_comment(message_id=100, user_id=7, comment='答非所问') is True
    assert len(calls) == 2


def test_backfill_gives_up_after_the_bounded_retries(monkeypatch):
    """A genuine miss must stay bounded — this is a best-effort path.

    The reason is already durable on `chatmessage.remark`, so retrying forever
    (or raising) would be worse than returning False.
    """
    calls = []

    def _always_miss(**kwargs):
        calls.append(kwargs)
        return False

    monkeypatch.setattr(MessageFeedbackEventDao, 'backfill_comment', staticmethod(_always_miss))
    monkeypatch.setattr(
        'bisheng.chat_session.domain.services.feedback_event_service.time.sleep',
        lambda _seconds: None,
    )

    assert FeedbackEventService.backfill_comment(message_id=100, user_id=7, comment='x') is False
    assert len(calls) == 3  # BACKFILL_ATTEMPTS, not unbounded
