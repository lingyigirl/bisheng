"""工作流对话轮次、并发执行权和已完成历史服务。"""

# ruff: noqa: RUF002 -- 中文 docstring 使用全角标点。

from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from sqlalchemy import func, or_, select, update
from sqlmodel import Session

from bisheng.chat_session.domain.services.history_context import (
    HistorySummary,
    HistoryTurn,
    count_text_tokens,
)
from bisheng.core.context.tenant import DEFAULT_TENANT_ID, bypass_tenant_filter
from bisheng.core.database import get_sync_db_session
from bisheng.database.models.chat_turn import ChatTurn, ChatTurnStatus, ChatTurnSummary, ChatTurnSummaryStatus
from bisheng.database.models.flow import Flow, FlowType
from bisheng.database.models.session import MessageSession

DEFAULT_STALE_AFTER_SECONDS = 30 * 60


@contextmanager
def _turn_db_session():
    """轮次表使用显式 user/tenant 条件，因此绕过 worker 环境租户过滤。"""
    with bypass_tenant_filter():
        with get_sync_db_session() as session:
            yield session


class ChatTurnBusyError(Exception):
    """同一会话已经存在运行中的轮次。"""


@dataclass
class HistoryContextData:
    turns: list[HistoryTurn] = field(default_factory=list)
    summaries: list[HistorySummary] = field(default_factory=list)


class ChatTurnService:
    @staticmethod
    def _clear_lock(session: Session, chat_id: str, turn_id: str) -> None:
        session.exec(
            update(MessageSession)
            .where(
                MessageSession.chat_id == chat_id,
                MessageSession.active_turn_id == turn_id,
            )
            .values(active_turn_id=None, active_turn_started_at=None)
        )

    @classmethod
    def _ensure_session(
        cls,
        session: Session,
        *,
        chat_id: str,
        flow_id: str,
        user_id: int,
        tenant_id: int,
    ) -> MessageSession:
        current = session.get(MessageSession, chat_id)
        if current is not None:
            return current

        flow = session.get(Flow, flow_id)
        current = MessageSession(
            chat_id=chat_id,
            flow_id=flow_id,
            flow_type=FlowType.WORKFLOW.value,
            flow_name=getattr(flow, "name", "") or "",
            user_id=user_id,
            tenant_id=tenant_id or DEFAULT_TENANT_ID,
            group_ids=[],
        )
        session.add(current)
        session.flush()
        return current

    @classmethod
    def start_turn(
        cls,
        *,
        turn_id: str,
        chat_id: str,
        flow_id: str,
        user_id: int,
        tenant_id: int,
        stale_after_seconds: int = DEFAULT_STALE_AFTER_SECONDS,
    ) -> ChatTurn:
        now = datetime.now()
        stale_before = now - timedelta(seconds=stale_after_seconds)
        with _turn_db_session() as session:
            message_session = cls._ensure_session(
                session,
                chat_id=chat_id,
                flow_id=flow_id,
                user_id=user_id,
                tenant_id=tenant_id,
            )
            session_tenant_id = message_session.tenant_id or tenant_id

            stale_turns = session.scalars(
                select(ChatTurn).where(
                    ChatTurn.chat_id == chat_id,
                    ChatTurn.user_id == user_id,
                    ChatTurn.status.in_([ChatTurnStatus.RUNNING.value, ChatTurnStatus.SUMMARIZING.value]),
                    ChatTurn.start_time < stale_before,
                )
            ).all()
            for stale_turn in stale_turns:
                stale_turn.status = ChatTurnStatus.FAILED.value
                stale_turn.error_code = "STALE_TURN"
                stale_turn.finish_time = now
                cls._clear_lock(session, chat_id, stale_turn.turn_id)

            claim = session.exec(
                update(MessageSession)
                .where(
                    MessageSession.chat_id == chat_id,
                    MessageSession.user_id == user_id,
                    MessageSession.tenant_id == session_tenant_id,
                    or_(
                        MessageSession.active_turn_id.is_(None),
                        MessageSession.active_turn_started_at.is_(None),
                        MessageSession.active_turn_started_at < stale_before,
                    ),
                    MessageSession.is_delete.is_(False),
                )
                .values(active_turn_id=turn_id, active_turn_started_at=now)
            )
            rowcount = getattr(claim, "rowcount", None)
            if rowcount == 0:
                session.rollback()
                raise ChatTurnBusyError(chat_id)

            max_turn_no = session.scalar(
                select(func.max(ChatTurn.turn_no)).where(
                    ChatTurn.chat_id == chat_id,
                    ChatTurn.user_id == user_id,
                )
            )
            turn = ChatTurn(
                turn_id=turn_id,
                chat_id=chat_id,
                flow_id=flow_id,
                user_id=user_id,
                tenant_id=session_tenant_id,
                turn_no=(max_turn_no or 0) + 1,
                status=ChatTurnStatus.RUNNING.value,
                start_time=now,
            )
            session.add(turn)
            session.commit()
            session.refresh(turn)
            return turn

    @classmethod
    def record_question(
        cls,
        *,
        turn_id: str,
        question_message_id: int,
        question_text: str,
    ) -> None:
        with _turn_db_session() as session:
            turn = session.get(ChatTurn, turn_id)
            if turn is None or turn.status != ChatTurnStatus.RUNNING.value:
                return
            turn.question_message_id = question_message_id
            turn.question_text = question_text
            session.add(turn)
            session.commit()

    @classmethod
    def record_answer(
        cls,
        *,
        turn_id: str,
        answer_message_id: int,
        answer_text: str,
    ) -> None:
        with _turn_db_session() as session:
            turn = session.get(ChatTurn, turn_id)
            if turn is None or turn.status not in (
                ChatTurnStatus.RUNNING.value,
                ChatTurnStatus.SUMMARIZING.value,
            ):
                return
            turn.answer_message_id = answer_message_id
            turn.answer_text = answer_text
            turn.turn_token_count = count_text_tokens(f"Human: {turn.question_text or ''}\nAI: {answer_text}")
            turn.status = ChatTurnStatus.SUMMARIZING.value
            session.add(turn)
            session.commit()

    @classmethod
    def mark_summary_ready(cls, *, turn_id: str) -> None:
        with _turn_db_session() as session:
            turn = session.get(ChatTurn, turn_id)
            if turn is None or turn.status != ChatTurnStatus.SUMMARIZING.value:
                return
            turn.status = ChatTurnStatus.COMPLETED.value
            turn.finish_time = datetime.now()
            cls._clear_lock(session, turn.chat_id, turn.turn_id)
            session.add(turn)
            session.commit()

    @classmethod
    def fail_turn(cls, *, turn_id: str, error_code: str | None = None) -> None:
        with _turn_db_session() as session:
            turn = session.get(ChatTurn, turn_id)
            if turn is None:
                return
            turn.status = ChatTurnStatus.FAILED.value
            turn.error_code = error_code
            turn.finish_time = datetime.now()
            cls._clear_lock(session, turn.chat_id, turn.turn_id)
            session.add(turn)
            session.commit()

    @classmethod
    def cancel_turn(cls, *, turn_id: str) -> None:
        with _turn_db_session() as session:
            turn = session.get(ChatTurn, turn_id)
            if turn is None:
                return
            turn.status = ChatTurnStatus.CANCELLED.value
            turn.finish_time = datetime.now()
            cls._clear_lock(session, turn.chat_id, turn.turn_id)
            session.add(turn)
            session.commit()

    @classmethod
    def get_turn(cls, turn_id: str) -> ChatTurn | None:
        with _turn_db_session() as session:
            return session.get(ChatTurn, turn_id)

    @classmethod
    def list_completed_history(
        cls,
        *,
        chat_id: str,
        user_id: int,
        tenant_id: int | None,
        before_turn_no: int,
        window: int,
    ) -> list[tuple[str, str]]:
        if window < 1:
            return []
        with _turn_db_session() as session:
            stmt = select(ChatTurn).where(
                ChatTurn.chat_id == chat_id,
                ChatTurn.user_id == user_id,
                ChatTurn.status == ChatTurnStatus.COMPLETED.value,
                ChatTurn.turn_no < before_turn_no,
            )
            if tenant_id is not None:
                stmt = stmt.where(ChatTurn.tenant_id == tenant_id)
            rows = list(session.scalars(stmt.order_by(ChatTurn.turn_no.desc()).limit(window)).all())
            rows.reverse()
            result: list[tuple[str, str]] = []
            for row in rows:
                if row.question_text:
                    result.append((row.question_text, "human"))
                if row.answer_text:
                    result.append((row.answer_text, "AI"))
            return result

    @classmethod
    def load_history_context(
        cls,
        *,
        chat_id: str,
        user_id: int,
        tenant_id: int | None,
        before_turn_no: int,
        raw_window: int,
    ) -> HistoryContextData:
        """读取最近完整轮次与可用的全部摘要，供 Token 预算选择。"""
        if raw_window < 1:
            return HistoryContextData()
        with get_sync_db_session() as session:
            turn_stmt = select(ChatTurn).where(
                ChatTurn.chat_id == chat_id,
                ChatTurn.user_id == user_id,
                ChatTurn.status == ChatTurnStatus.COMPLETED.value,
                ChatTurn.turn_no < before_turn_no,
            )
            summary_stmt = select(ChatTurnSummary).where(
                ChatTurnSummary.chat_id == chat_id,
                ChatTurnSummary.user_id == user_id,
                ChatTurnSummary.end_turn_no < before_turn_no,
                ChatTurnSummary.status == ChatTurnSummaryStatus.READY.value,
            )
            if tenant_id is not None:
                turn_stmt = turn_stmt.where(ChatTurn.tenant_id == tenant_id)
                summary_stmt = summary_stmt.where(ChatTurnSummary.tenant_id == tenant_id)
            turns = list(session.scalars(turn_stmt.order_by(ChatTurn.turn_no.desc()).limit(raw_window)).all())
            turns.reverse()
            summaries = list(session.scalars(summary_stmt).all())

            return HistoryContextData(
                turns=[
                    HistoryTurn(
                        turn_no=item.turn_no,
                        question_text=item.question_text or "",
                        answer_text=item.answer_text or "",
                        token_count=item.turn_token_count,
                    )
                    for item in turns
                ],
                summaries=[
                    HistorySummary(
                        start_turn_no=item.start_turn_no,
                        end_turn_no=item.end_turn_no,
                        depth=item.depth,
                        summary=item.summary,
                        token_count=item.token_count,
                        source_token_count=item.source_token_count,
                    )
                    for item in summaries
                ],
            )
