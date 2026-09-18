from __future__ import annotations

from datetime import datetime

from sqlalchemy import Column, DateTime, Integer, String, text
from sqlmodel import Field

from bisheng.common.models.base import SQLModelSerializable
from bisheng.core.database.dialect_helpers import JsonType

# Coarse error categories, kept as a small closed set so the admin failure list
# can group them. The raw driver message is NOT stored (it can carry the host,
# account or DSN); only the category and the vendor's own error code are.
CATEGORY_SYNTAX = 'syntax'
CATEGORY_UNKNOWN_TABLE = 'unknown_table'
CATEGORY_UNKNOWN_COLUMN = 'unknown_column'
CATEGORY_PERMISSION = 'permission'
CATEGORY_TIMEOUT = 'timeout'
CATEGORY_CONNECTION = 'connection'
CATEGORY_OTHER = 'other'


class DbQueryFailure(SQLModelSerializable, table=True):
    """A query that still failed after the retry budget was exhausted (AC-41).

    Exists to drive governance: an admin reads this list to find which tables
    are under-described and fill in their `business_desc` (AC-42).

    Storage boundary (INV-19 clause 3, continuing INV-11): the failing
    statement and a summary of the error. NOT the result set, and NOT the user's
    original question -- the question is conversation text, and this table is
    not a conversation store.
    """

    __tablename__ = 'db_query_failure'
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
    # Stored as JSON rather than LargeText: the retry loop produces several
    # statements and the admin list shows them together. Still statements only --
    # see the class docstring for what is excluded.
    statements: list[str] = Field(
        default_factory=list,
        sa_column=Column(
            JsonType,
            nullable=True,
            comment='失败语句列表 (重试过程中的各次尝试); 不存结果集、不存提问原文',
        ),
    )
    error_category: str = Field(
        sa_column=Column(String(32), nullable=False, index=True, comment='错误类别: syntax/unknown_table/...'),
    )
    vendor_code: str | None = Field(
        default=None,
        sa_column=Column(String(64), nullable=True, comment='数据库供应商错误码'),
    )
    tables: list[str] = Field(
        default_factory=list,
        sa_column=Column(
            JsonType,
            nullable=True,
            comment='涉及的表名列表; 供管理员定位需补全描述的表',
        ),
    )
    create_time: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime, nullable=False, server_default=text('CURRENT_TIMESTAMP'), index=True, comment='创建时间'),
    )
