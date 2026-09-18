from __future__ import annotations

from datetime import datetime

from sqlalchemy import Column, DateTime, Integer, String, text
from sqlmodel import Field

from bisheng.common.models.base import SQLModelSerializable
from bisheng.core.database.dialect_helpers import JsonType, LargeText

# How the schema reached the model, recorded so the full/retrieved threshold can
# be measured and tuned (spec AC-28). One enum shared by the execution record,
# the logs and the response -- do not invent a second name for the same thing.
INJECTION_FULL = 'full'
INJECTION_RETRIEVED = 'retrieved'

# Execution outcome. `blocked` is distinct from `failed`: the guard refused the
# statement before it reached the database (spec AC-16/17), which is not a
# database failure and must not enter the retry path.
STATUS_SUCCESS = 'success'
STATUS_FAILED = 'failed'
STATUS_BLOCKED = 'blocked'
STATUS_TRUNCATED = 'truncated'


class DbQueryLog(SQLModelSerializable, table=True):
    """One executed query (spec AC-22).

    Stores the statement and its outcome, and NOTHING else about the result:
    no result rows, no result-set contents (spec AC-23 / INV-19 clause 3). The
    row count is a status detail, not data -- it is what tells an admin whether
    a truncated result was the platform's doing.

    Same boundary as F056's hit log (INV-11): summaries and identifiers, never
    the payload.
    """

    __tablename__ = 'db_query_log'
    __table_args__ = ({'mysql_charset': 'utf8mb4', 'mysql_collate': 'utf8mb4_unicode_ci'},)

    id: int | None = Field(default=None, primary_key=True)
    tenant_id: int = Field(
        default=None,
        sa_column=Column(Integer, nullable=False, index=True, comment='租户ID'),
    )
    user_id: int | None = Field(
        default=None,
        sa_column=Column(Integer, nullable=True, index=True, comment='操作人ID'),
    )
    datasource_id: int = Field(
        sa_column=Column(Integer, nullable=False, index=True, comment='数据源ID'),
    )
    # Which tables the statement touched. A summary for the audit trail, not the
    # data itself.
    tables: list[str] = Field(
        default_factory=list,
        sa_column=Column(JsonType, nullable=True, comment='涉及的表名列表'),
    )
    statement: str = Field(
        sa_column=Column(LargeText, nullable=False, comment='执行的语句; 不存结果集'),
    )
    status: str = Field(
        sa_column=Column(String(16), nullable=False, index=True, comment='执行状态: success/failed/blocked/truncated'),
    )
    row_count: int | None = Field(
        default=None,
        sa_column=Column(Integer, nullable=True, comment='返回行数 (截断后实际取回的行数)'),
    )
    truncated: int = Field(
        default=0,
        sa_column=Column(Integer, nullable=False, server_default=text('0'), comment='是否因超行数上限被截断: 1=是'),
    )
    # Degradation flags, each recording that a fallback happened on this query:
    # a read-only session could not be established (spec AC-19), or the SQL
    # parser could not handle the dialect so the narrow validator ran instead
    # (spec AC-20). Both must be observable, not silent.
    readonly_degraded: int = Field(
        default=0,
        sa_column=Column(Integer, nullable=False, server_default=text('0'), comment='只读会话不可用而降级: 1=是'),
    )
    guard_degraded: int = Field(
        default=0,
        sa_column=Column(Integer, nullable=False, server_default=text('0'), comment='SQL解析失败退窄校验: 1=是'),
    )
    injection_mode: str | None = Field(
        default=None,
        sa_column=Column(String(16), nullable=True, comment='schema注入形态: full/retrieved (AC-28)'),
    )
    duration_ms: int | None = Field(
        default=None,
        sa_column=Column(Integer, nullable=True, comment='执行耗时(毫秒)'),
    )
    create_time: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime, nullable=False, server_default=text('CURRENT_TIMESTAMP'), index=True, comment='创建时间'),
    )
