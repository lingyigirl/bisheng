from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import Column, DateTime, Integer, String, text
from sqlmodel import Field

from bisheng.common.models.base import SQLModelSerializable
from bisheng.core.database.dialect_helpers import UPDATE_TIME_SERVER_DEFAULT, JsonType, LargeText


class DbDatasourceTable(SQLModelSerializable, table=True):
    """One table exposed from a datasource, plus its governed description.

    Two fields with different owners, and conflating them is a bug:

      * `structure_snapshot` — machine-collected (name / type / in-database
        comment for every column). A re-collect REPLACES it.
      * `business_desc` — written by an admin. A re-collect must NEVER touch it
        (spec AC-12): the whole point of the description is to survive schema
        churn, and a blanket `update` is the easy way to lose it.

    No sample values are ever collected -- only structure, never table rows
    (spec AC-13).
    """

    __tablename__ = 'db_datasource_table'
    __table_args__ = ({'mysql_charset': 'utf8mb4', 'mysql_collate': 'utf8mb4_unicode_ci'},)

    id: int | None = Field(default=None, primary_key=True)
    tenant_id: int = Field(
        default=None,
        sa_column=Column(Integer, nullable=False, index=True, comment='租户ID'),
    )
    datasource_id: int = Field(
        sa_column=Column(Integer, nullable=False, index=True, comment='所属数据源ID'),
    )
    table_name: str = Field(
        sa_column=Column(String(255), nullable=False, index=True, comment='表名'),
    )
    table_comment: str | None = Field(
        default=None,
        sa_column=Column(LargeText, nullable=True, comment='库内表注释 (采集所得, 可能为空)'),
    )
    structure_snapshot: list[dict[str, Any]] = Field(
        default_factory=list,
        sa_column=Column(
            JsonType,
            nullable=True,
            comment='结构快照 [{"name","type","comment"}]；重采集只刷它，不覆盖 business_desc',  # noqa: RUF001 (fullwidth punctuation in a Chinese column comment)
        ),
    )
    business_desc: str | None = Field(
        default=None,
        sa_column=Column(LargeText, nullable=True, comment='人工维护的业务描述; 重采集不得覆盖'),
    )
    enabled: int = Field(
        default=1,
        sa_column=Column(Integer, nullable=False, server_default=text('1'), comment='是否暴露给模型: 1=是 0=否'),
    )
    create_time: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime, nullable=False, server_default=text('CURRENT_TIMESTAMP'), comment='创建时间'),
    )
    update_time: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime, nullable=False, server_default=UPDATE_TIME_SERVER_DEFAULT, comment='更新时间'),
    )
