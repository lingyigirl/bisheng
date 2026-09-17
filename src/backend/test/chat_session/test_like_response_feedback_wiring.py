"""T012 (F059): `like_response` / `comment_on_answer` actually feed the audit trail.

`test_feedback_event_service.py` covers the service in isolation, with the DAO
stubbed. This file covers the opposite seam: that the EXISTING rating chain calls
it, with the right from/to values, and that the audit adds no new failure mode to
a path users depend on.

The from/to capture is the subtle part — `like_response` mutates `message.liked`
part-way through, so a misplaced call would record the new value as both "from"
and "to" (and the service would then discard it as a no-op, silently losing every
event).
"""

from types import SimpleNamespace

import pytest

from bisheng.chat_session.domain.services.chat_message_service import ChatMessageService
from bisheng.chat_session.domain.services.feedback_event_service import FeedbackEventService
from bisheng.database.models.message import LikedType


class _Recorded:
    def __init__(self):
        self.events = []
        self.backfills = []
        self.raise_on_record = None

    def record(self, **kwargs):
        if self.raise_on_record:
            raise self.raise_on_record
        self.events.append(kwargs)

    def backfill_comment(self, **kwargs):
        self.backfills.append(kwargs)
        return True


@pytest.fixture
def audit(monkeypatch):
    rec = _Recorded()
    monkeypatch.setattr(FeedbackEventService, 'record', staticmethod(rec.record))
    monkeypatch.setattr(FeedbackEventService, 'backfill_comment', staticmethod(rec.backfill_comment))
    return rec


@pytest.fixture
def message(monkeypatch):
    """A saved message whose verdict the service will mutate."""

    def _install(liked=0, flow_id='app-1', chat_id='chat-1'):
        msg = SimpleNamespace(id=100, liked=liked, flow_id=flow_id, chat_id=chat_id, copied=0)
        monkeypatch.setattr(
            'bisheng.chat_session.domain.services.chat_message_service.ChatMessageDao.get_message_by_id',
            staticmethod(lambda mid: msg),
        )
        monkeypatch.setattr(
            'bisheng.chat_session.domain.services.chat_message_service.ChatMessageDao.update_message_model',
            staticmethod(lambda m: None),
        )
        monkeypatch.setattr(
            'bisheng.chat_session.domain.services.chat_message_service.MessageSessionDao.add_like_count',
            staticmethod(lambda *a, **kw: None),
        )
        monkeypatch.setattr(
            'bisheng.chat_session.domain.services.chat_message_service.MessageSessionDao.add_dislike_count',
            staticmethod(lambda *a, **kw: None),
        )
        monkeypatch.setattr(
            'bisheng.chat_session.domain.services.chat_message_service.get_current_tenant_id',
            lambda: 1,
        )
        return msg

    return _install


@pytest.mark.parametrize('start, new', [(0, 1), (1, 0), (0, 2), (2, 0), (1, 2), (2, 1)])
def test_like_response_records_the_real_before_and_after(audit, message, start, new):
    """The "from" value must be the verdict BEFORE the mutation."""
    message(liked=start)
    login_user = SimpleNamespace(user_id=7, tenant_id=1)

    ChatMessageService.like_response(SimpleNamespace(message_id=100, liked=new), login_user)

    assert len(audit.events) == 1
    event = audit.events[0]
    assert event['from_value'] == start, 'from_value must be captured before the overwrite'
    assert event['to_value'] == new
    assert event['message_id'] == 100
    assert event['user_id'] == 7
    assert event['flow_id'] == 'app-1'
    assert event['tenant_id'] == 1


def test_unchanged_verdict_records_nothing(audit, message):
    """The early return must stay ahead of the audit write (spec AC-27)."""
    message(liked=1)

    ChatMessageService.like_response(
        SimpleNamespace(message_id=100, liked=1), SimpleNamespace(user_id=7, tenant_id=1)
    )

    assert audit.events == []


def test_a_failing_audit_does_not_break_the_rating(audit, message):
    """Spec AC-24: the verdict still lands even when the audit write fails.

    The user's like must not turn into an error because an audit row could not be
    written — `liked` is the source of truth and it is already persisted.
    """
    msg = message(liked=0)
    audit.raise_on_record = RuntimeError('audit down')

    ChatMessageService.like_response(
        SimpleNamespace(message_id=100, liked=1), SimpleNamespace(user_id=7, tenant_id=1)
    )

    assert msg.liked == LikedType.LIKED.value  # verdict still applied


def test_a_missing_login_user_does_not_break_the_rating(audit, message):
    """Legacy callers invoke `like_response(data)` with no user; that must work.

    The audit then has no attribution, but the rating itself is unaffected.
    """
    msg = message(liked=0)

    ChatMessageService.like_response(SimpleNamespace(message_id=100, liked=1))

    assert msg.liked == LikedType.LIKED.value
    assert audit.events[0]['user_id'] is None


def test_comment_on_answer_backfills_the_reason(audit, message, monkeypatch):
    monkeypatch.setattr(
        'bisheng.chat_session.domain.services.chat_message_service.comment_answer',
        lambda message_id, comment: None,
    )

    ChatMessageService.comment_on_answer(100, '答非所问', SimpleNamespace(user_id=7, tenant_id=1))

    assert len(audit.backfills) == 1
    assert audit.backfills[0]['message_id'] == 100
    assert audit.backfills[0]['user_id'] == 7
    assert audit.backfills[0]['comment'] == '答非所问'


def test_comment_backfill_failure_does_not_break_the_comment(audit, message, monkeypatch):
    """The reason is already durable on `chatmessage.remark` before this runs."""
    saved = {}

    def _comment_answer(message_id, comment):
        saved['remark'] = comment

    monkeypatch.setattr(
        'bisheng.chat_session.domain.services.chat_message_service.comment_answer', _comment_answer
    )

    def _boom(**kwargs):
        raise RuntimeError('audit down')

    monkeypatch.setattr(FeedbackEventService, 'backfill_comment', staticmethod(_boom))

    ChatMessageService.comment_on_answer(100, '答非所问', SimpleNamespace(user_id=7, tenant_id=1))

    assert saved['remark'] == '答非所问'
