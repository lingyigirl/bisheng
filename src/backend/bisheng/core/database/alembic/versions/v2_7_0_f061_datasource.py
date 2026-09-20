"""F061: datasource registry, exposed-table metadata, query log and failure trail.

Four new tables plus one column on the existing `assistantlink`:

  * `db_datasource`         — a registered database and its credentials
  * `db_datasource_table`   — one exposed table: machine-collected structure
                              snapshot + human-maintained business description
  * `db_query_log`          — one row per executed query
  * `db_query_failure`      — queries that failed after the retry budget

Why these tables carry no foreign keys
--------------------------------------
Same reasoning as F058's run table. `db_datasource_table.datasource_id` and the
two log tables' `datasource_id` are plain integers. The referenced rows are
soft-referenced rather than constrained: spec AC-62 requires that deleting a
datasource leaves history (answers, citations, execution records) readable, and
a FK with cascade would delete exactly the audit trail the spec wants kept,
while a FK without cascade would block the delete.

Why `assistantlink.datasource_id` is a NEW column rather than reusing `tool_id`
------------------------------------------------------------------------------
`assistantlink` already carries `tool_id` / `flow_id` / `knowledge_id`, one
column per bindable resource kind. Datasources must be their own kind: storing
them in `tool_id` would make them inherit the `tool` resource's authorization
and management surface, which contradicts spec AC-43 (datasource is an
independent controlled resource, a peer of the knowledge library, not a
subtype) and AC-44 (it has its own permission point). NULL means "no datasource
bound", which is every existing row -- so existing assistants are unaffected
(spec AC-60).

DEV NOTE ON THE TABLE NAME: `AssistantLink.__tablename__` is `assistantlink`
(SQLModel lowercases the class name without inserting an underscore). The
migration must use that exact spelling -- `assistant_link` does not exist, and
`column_exists` would happily report the column missing on a table that is
simply not there.

Why the column is nullable with no server_default
--------------------------------------------------
No default, so "not bound" stays distinguishable from "bound to datasource 0".
Every existing row reads NULL, which is the correct meaning.

DDL-only
--------
Per `alembic/AGENTS.md` §5 this revision contains schema DDL only. No data
effect beyond the DDL itself, and none is needed here: the new tables start
empty and the new column starts NULL.

Revision ID: f061_datasource
Revises: f058_doc_extract
Create Date: 2026-09-18
"""

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op

from bisheng.core.database.dialect_helpers import (
    JsonType,
    LargeText,
    column_exists,
    index_exists,
    table_exists,
    update_time_server_default,
)

revision: str = "f061_datasource"
down_revision: Union[str, Sequence[str], None] = "f058_doc_extract"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_DATASOURCE_TABLE = "db_datasource"
_DATASOURCE_TABLE_META = "db_datasource_table"
_QUERY_LOG_TABLE = "db_query_log"
_QUERY_FAILURE_TABLE = "db_query_failure"

# Not `assistant_link` -- see the note in the module docstring.
_ASSISTANT_LINK_TABLE = "assistantlink"
_ASSISTANT_LINK_COLUMN = "datasource_id"


def upgrade() -> None:
    # Every step is guarded. This table set may already exist on a fresh
    # database, because an online upgrade runs `create_all(checkfirst=True)` at
    # the CURRENT model shape before replaying revisions, while an existing
    # deployment has neither the tables nor the column. Both paths must
    # converge on the same end state.
    conn = op.get_bind()

    if not table_exists(conn, _DATASOURCE_TABLE):
        op.create_table(
            _DATASOURCE_TABLE,
            sa.Column('id', sa.Integer, primary_key=True, autoincrement=True),
            sa.Column('tenant_id', sa.Integer, nullable=False, comment='租户ID'),
            sa.Column('user_id', sa.Integer, nullable=True, comment='创建人ID'),
            sa.Column('name', sa.String(128), nullable=False, comment='数据源名称'),
            sa.Column('engine', sa.String(32), nullable=False,
                      comment='数据库引擎: mysql/postgresql/gaussdb/oracle/db2/dm'),
            sa.Column('host', sa.String(255), nullable=False, comment='主机'),
            sa.Column('port', sa.Integer, nullable=False, comment='端口'),
            sa.Column('database_name', sa.String(128), nullable=False, comment='库名'),
            sa.Column('username', sa.String(128), nullable=False, comment='账号'),
            sa.Column('password', LargeText, nullable=False,
                      comment='密码密文 (Fernet); 任何读取路径不回显'),
            sa.Column('description', LargeText, nullable=True, comment='数据源用途说明'),
            sa.Column('writable_account', sa.Integer, nullable=True,
                      comment='账号是否具备写权限: 1=是 0=否 NULL=未检测'),
            sa.Column('enabled', sa.Integer, nullable=False, server_default='1',
                      comment='启用位: 1=启用 0=停用'),
            sa.Column('create_time', sa.DateTime, nullable=False,
                      server_default=sa.text('CURRENT_TIMESTAMP')),
            sa.Column('update_time', sa.DateTime, nullable=False,
                      server_default=update_time_server_default(conn)),
        )
    if not index_exists(conn, _DATASOURCE_TABLE, 'ix_db_datasource_tenant_id'):
        op.create_index('ix_db_datasource_tenant_id', _DATASOURCE_TABLE, ['tenant_id'])
    if not index_exists(conn, _DATASOURCE_TABLE, 'ix_db_datasource_name'):
        op.create_index('ix_db_datasource_name', _DATASOURCE_TABLE, ['name'])
    if not index_exists(conn, _DATASOURCE_TABLE, 'ix_db_datasource_user_id'):
        op.create_index('ix_db_datasource_user_id', _DATASOURCE_TABLE, ['user_id'])

    if not table_exists(conn, _DATASOURCE_TABLE_META):
        op.create_table(
            _DATASOURCE_TABLE_META,
            sa.Column('id', sa.Integer, primary_key=True, autoincrement=True),
            sa.Column('tenant_id', sa.Integer, nullable=False, comment='租户ID'),
            sa.Column('datasource_id', sa.Integer, nullable=False, comment='所属数据源ID'),
            sa.Column('table_name', sa.String(255), nullable=False, comment='表名'),
            sa.Column('table_comment', LargeText, nullable=True,
                      comment='库内表注释 (采集所得, 可能为空)'),
            sa.Column('structure_snapshot', JsonType, nullable=True,
                      comment='结构快照 [{"name","type","comment"}]; 重采集只刷它'),
            sa.Column('business_desc', LargeText, nullable=True,
                      comment='人工维护的业务描述; 重采集不得覆盖'),
            sa.Column('enabled', sa.Integer, nullable=False, server_default='1',
                      comment='是否暴露给模型: 1=是 0=否'),
            sa.Column('create_time', sa.DateTime, nullable=False,
                      server_default=sa.text('CURRENT_TIMESTAMP')),
            sa.Column('update_time', sa.DateTime, nullable=False,
                      server_default=update_time_server_default(conn)),
        )
    if not index_exists(conn, _DATASOURCE_TABLE_META, 'ix_db_datasource_table_tenant_id'):
        op.create_index('ix_db_datasource_table_tenant_id', _DATASOURCE_TABLE_META, ['tenant_id'])
    if not index_exists(conn, _DATASOURCE_TABLE_META, 'ix_db_datasource_table_datasource_id'):
        op.create_index('ix_db_datasource_table_datasource_id', _DATASOURCE_TABLE_META, ['datasource_id'])
    if not index_exists(conn, _DATASOURCE_TABLE_META, 'ix_db_datasource_table_table_name'):
        op.create_index('ix_db_datasource_table_table_name', _DATASOURCE_TABLE_META, ['table_name'])

    if not table_exists(conn, _QUERY_LOG_TABLE):
        op.create_table(
            _QUERY_LOG_TABLE,
            sa.Column('id', sa.Integer, primary_key=True, autoincrement=True),
            sa.Column('tenant_id', sa.Integer, nullable=False, comment='租户ID'),
            sa.Column('user_id', sa.Integer, nullable=True, comment='操作人ID'),
            sa.Column('datasource_id', sa.Integer, nullable=False, comment='数据源ID'),
            sa.Column('tables', JsonType, nullable=True, comment='涉及的表名列表'),
            sa.Column('statement', LargeText, nullable=False, comment='执行的语句; 不存结果集'),
            sa.Column('status', sa.String(16), nullable=False,
                      comment='执行状态: success/failed/blocked/truncated'),
            sa.Column('row_count', sa.Integer, nullable=True,
                      comment='返回行数 (截断后实际取回的行数)'),
            sa.Column('truncated', sa.Integer, nullable=False, server_default='0',
                      comment='是否因超行数上限被截断: 1=是'),
            sa.Column('readonly_degraded', sa.Integer, nullable=False, server_default='0',
                      comment='只读会话不可用而降级: 1=是'),
            sa.Column('guard_degraded', sa.Integer, nullable=False, server_default='0',
                      comment='SQL解析失败退窄校验: 1=是'),
            sa.Column('injection_mode', sa.String(16), nullable=True,
                      comment='schema注入形态: full/retrieved'),
            sa.Column('duration_ms', sa.Integer, nullable=True, comment='执行耗时(毫秒)'),
            sa.Column('create_time', sa.DateTime, nullable=False,
                      server_default=sa.text('CURRENT_TIMESTAMP')),
        )
    if not index_exists(conn, _QUERY_LOG_TABLE, 'ix_db_query_log_tenant_id'):
        op.create_index('ix_db_query_log_tenant_id', _QUERY_LOG_TABLE, ['tenant_id'])
    if not index_exists(conn, _QUERY_LOG_TABLE, 'ix_db_query_log_datasource_id'):
        op.create_index('ix_db_query_log_datasource_id', _QUERY_LOG_TABLE, ['datasource_id'])
    if not index_exists(conn, _QUERY_LOG_TABLE, 'ix_db_query_log_user_id'):
        op.create_index('ix_db_query_log_user_id', _QUERY_LOG_TABLE, ['user_id'])
    if not index_exists(conn, _QUERY_LOG_TABLE, 'ix_db_query_log_status'):
        op.create_index('ix_db_query_log_status', _QUERY_LOG_TABLE, ['status'])
    if not index_exists(conn, _QUERY_LOG_TABLE, 'ix_db_query_log_create_time'):
        op.create_index('ix_db_query_log_create_time', _QUERY_LOG_TABLE, ['create_time'])

    if not table_exists(conn, _QUERY_FAILURE_TABLE):
        op.create_table(
            _QUERY_FAILURE_TABLE,
            sa.Column('id', sa.Integer, primary_key=True, autoincrement=True),
            sa.Column('tenant_id', sa.Integer, nullable=False, comment='租户ID'),
            sa.Column('user_id', sa.Integer, nullable=True, comment='操作人ID'),
            sa.Column('datasource_id', sa.Integer, nullable=False, comment='数据源ID'),
            sa.Column('statements', JsonType, nullable=True,
                      comment='失败语句列表; 不存结果集、不存提问原文'),
            sa.Column('error_category', sa.String(32), nullable=False,
                      comment='错误类别: syntax/unknown_table/unknown_column/permission/timeout/connection/other'),
            sa.Column('vendor_code', sa.String(64), nullable=True, comment='数据库供应商错误码'),
            sa.Column('tables', JsonType, nullable=True,
                      comment='涉及的表名列表; 供管理员定位需补全描述的表'),
            sa.Column('create_time', sa.DateTime, nullable=False,
                      server_default=sa.text('CURRENT_TIMESTAMP')),
        )
    if not index_exists(conn, _QUERY_FAILURE_TABLE, 'ix_db_query_failure_tenant_id'):
        op.create_index('ix_db_query_failure_tenant_id', _QUERY_FAILURE_TABLE, ['tenant_id'])
    if not index_exists(conn, _QUERY_FAILURE_TABLE, 'ix_db_query_failure_datasource_id'):
        op.create_index('ix_db_query_failure_datasource_id', _QUERY_FAILURE_TABLE, ['datasource_id'])
    if not index_exists(conn, _QUERY_FAILURE_TABLE, 'ix_db_query_failure_user_id'):
        op.create_index('ix_db_query_failure_user_id', _QUERY_FAILURE_TABLE, ['user_id'])
    if not index_exists(conn, _QUERY_FAILURE_TABLE, 'ix_db_query_failure_error_category'):
        op.create_index('ix_db_query_failure_error_category', _QUERY_FAILURE_TABLE, ['error_category'])
    if not index_exists(conn, _QUERY_FAILURE_TABLE, 'ix_db_query_failure_create_time'):
        op.create_index('ix_db_query_failure_create_time', _QUERY_FAILURE_TABLE, ['create_time'])

    # The binding column. Nullable and without a default so "not bound" stays
    # distinguishable from "bound to datasource 0"; every existing row reads
    # NULL, which is the correct meaning and keeps AC-60 intact.
    if not column_exists(conn, _ASSISTANT_LINK_TABLE, _ASSISTANT_LINK_COLUMN):
        op.add_column(
            _ASSISTANT_LINK_TABLE,
            sa.Column(
                _ASSISTANT_LINK_COLUMN,
                sa.Integer,
                nullable=True,
                comment='F061 绑定的数据源ID; NULL=未绑定',
            ),
        )
    # The index the ORM declares via `index=True`. Without it the two paths
    # diverge: `create_all` builds it on a fresh database, this migration did
    # not, so an upgraded deployment would silently lack an index a new one
    # has. Guarded like every other step.
    if not index_exists(conn, _ASSISTANT_LINK_TABLE, 'ix_assistantlink_datasource_id'):
        op.create_index('ix_assistantlink_datasource_id', _ASSISTANT_LINK_TABLE, [_ASSISTANT_LINK_COLUMN])


def downgrade() -> None:
    conn = op.get_bind()

    if index_exists(conn, _ASSISTANT_LINK_TABLE, 'ix_assistantlink_datasource_id'):
        op.drop_index('ix_assistantlink_datasource_id', table_name=_ASSISTANT_LINK_TABLE)
    if column_exists(conn, _ASSISTANT_LINK_TABLE, _ASSISTANT_LINK_COLUMN):
        op.drop_column(_ASSISTANT_LINK_TABLE, _ASSISTANT_LINK_COLUMN)

    # Drop in dependency order (none enforced by FKs, but the metadata table is
    # meaningless without its datasource). Indexes are dropped with their table.
    for table, indexes in (
        (
            _QUERY_FAILURE_TABLE,
            (
                'ix_db_query_failure_create_time',
                'ix_db_query_failure_error_category',
                'ix_db_query_failure_user_id',
                'ix_db_query_failure_datasource_id',
                'ix_db_query_failure_tenant_id',
            ),
        ),
        (
            _QUERY_LOG_TABLE,
            (
                'ix_db_query_log_create_time',
                'ix_db_query_log_status',
                'ix_db_query_log_user_id',
                'ix_db_query_log_datasource_id',
                'ix_db_query_log_tenant_id',
            ),
        ),
        (
            _DATASOURCE_TABLE_META,
            (
                'ix_db_datasource_table_table_name',
                'ix_db_datasource_table_datasource_id',
                'ix_db_datasource_table_tenant_id',
            ),
        ),
        (
            _DATASOURCE_TABLE,
            (
                'ix_db_datasource_user_id',
                'ix_db_datasource_name',
                'ix_db_datasource_tenant_id',
            ),
        ),
    ):
        if not table_exists(conn, table):
            continue
        for index in indexes:
            if index_exists(conn, table, index):
                op.drop_index(index, table_name=table)
        op.drop_table(table)
