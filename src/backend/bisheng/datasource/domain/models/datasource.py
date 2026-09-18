from __future__ import annotations

from datetime import datetime

from sqlalchemy import Column, DateTime, Integer, String, text
from sqlmodel import Field

from bisheng.common.models.base import SQLModelSerializable
from bisheng.core.database.dialect_helpers import UPDATE_TIME_SERVER_DEFAULT, LargeText

# Supported engines (spec AC-02). Plain strings with named constants rather
# than an Enum: the column is a string, and a row may carry a value the platform
# later drops -- an Enum field would make reading such a row raise at load time.
MYSQL = 'mysql'
POSTGRESQL = 'postgresql'
GAUSSDB = 'gaussdb'
ORACLE = 'oracle'
DB2 = 'db2'
DAMENG = 'dm'

SUPPORTED_ENGINES: frozenset[str] = frozenset({MYSQL, POSTGRESQL, GAUSSDB, ORACLE, DB2, DAMENG})


class DbDatasource(SQLModelSerializable, table=True):
    """A registered database the platform may query (spec AC-01).

    Owns its connection credentials. `password` holds Fernet ciphertext and is
    never returned by any read path (spec AC-04 / INV-19 clause 1) -- redaction
    happens where the response is built, not here.

    There is no row-level filtering anywhere in this feature: what a user can
    read is exactly what the configured database account can read (INV-19
    clause 4, an explicitly accepted risk).
    """

    __tablename__ = 'db_datasource'
    __table_args__ = ({'mysql_charset': 'utf8mb4', 'mysql_collate': 'utf8mb4_unicode_ci'},)

    id: int | None = Field(default=None, primary_key=True)
    tenant_id: int = Field(
        default=None,
        sa_column=Column(Integer, nullable=False, index=True, comment='租户ID'),
    )
    user_id: int | None = Field(
        default=None,
        sa_column=Column(Integer, nullable=True, index=True, comment='创建人ID'),
    )
    name: str = Field(
        sa_column=Column(String(128), nullable=False, index=True, comment='数据源名称'),
    )
    engine: str = Field(
        sa_column=Column(String(32), nullable=False, comment='数据库引擎: mysql/postgresql/gaussdb/oracle/db2/dm'),
    )
    host: str = Field(
        sa_column=Column(String(255), nullable=False, comment='主机'),
    )
    port: int = Field(
        sa_column=Column(Integer, nullable=False, comment='端口'),
    )
    database_name: str = Field(
        sa_column=Column(String(128), nullable=False, comment='库名'),
    )
    username: str = Field(
        sa_column=Column(String(128), nullable=False, comment='账号'),
    )
    password: str = Field(
        sa_column=Column(LargeText, nullable=False, comment='密码密文 (Fernet); 任何读取路径不回显'),
    )
    description: str | None = Field(
        default=None,
        sa_column=Column(LargeText, nullable=True, comment='数据源用途说明'),
    )
    # Set from the test-connection result; a writable account warns at save time
    # but does NOT block (spec AC-05). NULL = not yet determined.
    writable_account: int | None = Field(
        default=None,
        sa_column=Column(Integer, nullable=True, comment='账号是否具备写权限: 1=是 0=否 NULL=未检测'),
    )
    enabled: int = Field(
        default=1,
        sa_column=Column(Integer, nullable=False, server_default=text('1'), comment='启用位: 1=启用 0=停用'),
    )
    create_time: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime, nullable=False, server_default=text('CURRENT_TIMESTAMP'), comment='创建时间'),
    )
    update_time: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime, nullable=False, server_default=UPDATE_TIME_SERVER_DEFAULT, comment='更新时间'),
    )
