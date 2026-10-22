"""会话轮次与分层摘要的数据模型。"""

from datetime import datetime
from enum import StrEnum

from sqlalchemy import Column, DateTime, Integer, UniqueConstraint, text
from sqlmodel import Field

from bisheng.common.models.base import SQLModelSerializable
from bisheng.core.database.dialect_helpers import LargeText


class ChatTurnStatus(StrEnum):
    RUNNING = "running"
    SUMMARIZING = "summarizing"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class ChatTurnSummaryStatus(StrEnum):
    BUILDING = "building"
    READY = "ready"
    STALE = "stale"
    FAILED = "failed"
    REJECTED = "rejected"


class ChatTurn(SQLModelSerializable, table=True):
    __tablename__ = "chat_turn"
    __table_args__ = (
        UniqueConstraint("chat_id", "turn_no", name="uk_chat_turn_chat_no"),
        {"mysql_charset": "utf8mb4", "mysql_collate": "utf8mb4_unicode_ci"},
    )

    turn_id: str = Field(primary_key=True, max_length=128)
    chat_id: str = Field(index=True, max_length=64)
    flow_id: str = Field(index=True, max_length=64)
    user_id: int = Field(index=True)
    tenant_id: int = Field(index=True)
    turn_no: int
    status: str = Field(default=ChatTurnStatus.RUNNING.value, max_length=16, index=True)
    question_text: str | None = Field(default=None, sa_column=Column(LargeText, nullable=True))
    answer_text: str | None = Field(default=None, sa_column=Column(LargeText, nullable=True))
    question_message_id: int | None = None
    answer_message_id: int | None = None
    turn_token_count: int = Field(default=0, sa_column=Column(Integer, nullable=False, server_default=text("0")))
    error_code: str | None = Field(default=None, max_length=64)
    start_time: datetime = Field(
        default_factory=datetime.now,
        sa_column=Column(DateTime, nullable=False, server_default=text("CURRENT_TIMESTAMP"), index=True),
    )
    finish_time: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime, nullable=True),
    )


class ChatTurnSummary(SQLModelSerializable, table=True):
    __tablename__ = "chat_turn_summary"
    __table_args__ = (
        UniqueConstraint(
            "chat_id",
            "depth",
            "start_turn_no",
            "end_turn_no",
            name="uk_chat_turn_summary_range",
        ),
        {"mysql_charset": "utf8mb4", "mysql_collate": "utf8mb4_unicode_ci"},
    )

    summary_id: str = Field(primary_key=True, max_length=64)
    chat_id: str = Field(index=True, max_length=64)
    flow_id: str = Field(index=True, max_length=64)
    user_id: int = Field(index=True)
    tenant_id: int = Field(index=True)
    start_turn_no: int
    end_turn_no: int
    depth: int = Field(default=0, sa_column=Column(Integer, nullable=False, server_default=text("0")))
    summary: str = Field(sa_column=Column(LargeText, nullable=False))
    token_count: int
    source_token_count: int
    context_token_count: int = Field(
        default=0,
        sa_column=Column(Integer, nullable=False, server_default=text("0")),
    )
    source_turn_count: int
    status: str = Field(default=ChatTurnSummaryStatus.BUILDING.value, max_length=16, index=True)
    generator_model: str | None = Field(default=None, max_length=128)
    source_digest: str = Field(max_length=64)
    create_time: datetime = Field(
        default_factory=datetime.now,
        sa_column=Column(DateTime, nullable=False, server_default=text("CURRENT_TIMESTAMP")),
    )
    update_time: datetime = Field(
        default_factory=datetime.now,
        sa_column=Column(DateTime, nullable=False, server_default=text("CURRENT_TIMESTAMP")),
    )


class ChatTurnRead(ChatTurn):
    pass
