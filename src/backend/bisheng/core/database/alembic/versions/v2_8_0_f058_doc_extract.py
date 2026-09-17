"""F058: document extraction templates and extraction runs.

Two new tables backing the per-document-type structured extraction feature:
`doc_extract_template` (admin-configured field schema + render form per doc
type) and `doc_extract_run` (traceability record for each extraction).

Why `doc_type` is indexed but NOT unique
----------------------------------------
Template deletes are soft (`is_deleted`). A `UNIQUE(tenant_id, doc_type)`
constraint would count the deleted row too, so re-creating a template with a
freed identifier -- which the spec requires to work -- would fail with an
opaque constraint error. Uniqueness is therefore enforced in the service layer
against live rows only. See design decision 9 and pitfall 11.

Why `doc_extract_run` has no tenant_id uniqueness or FK to the template
----------------------------------------------------------------------
`template_id` intentionally has no foreign key: templates are soft-deleted, so
the row always survives, and a FK would add a constraint on a hot audit path
for no benefit. `doc_type` is denormalized onto the run so history stays
readable even after the template is deleted or renamed.

Revision ID: f058_doc_extract
Revises: f059_hit_log_source
Create Date: 2026-09-17
"""

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op

from bisheng.core.database.dialect_helpers import (
    JsonType,
    LargeText,
    index_exists,
    table_exists,
    update_time_server_default,
)

revision: str = "f058_doc_extract"
down_revision: Union[str, Sequence[str], None] = "f059_hit_log_source"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TEMPLATE_TABLE = "doc_extract_template"
_RUN_TABLE = "doc_extract_run"


def upgrade() -> None:
    # Guarded because model-created tables are built at their CURRENT model shape
    # by `create_all(checkfirst=True)`: on a fresh database the tables may already
    # exist, while an existing deployment does not have them. Both paths must
    # converge on the same end state.
    conn = op.get_bind()

    if not table_exists(conn, _TEMPLATE_TABLE):
        op.create_table(
            _TEMPLATE_TABLE,
            sa.Column('id', sa.BigInteger, primary_key=True, autoincrement=True),
            sa.Column('tenant_id', sa.Integer, nullable=False, comment='租户ID'),
            sa.Column('name', sa.String(128), nullable=False, comment='模板名称'),
            sa.Column('doc_type', sa.String(64), nullable=False, comment='文档类型标识'),
            sa.Column('description', sa.Text, nullable=False,
                      comment='适用范围：分类时喂给模型的该类描述'),
            sa.Column('field_schema', JsonType, nullable=True, comment='字段定义数组'),
            sa.Column('render_template', LargeText, nullable=False,
                      server_default='', comment='呈现形式，可为空'),
            sa.Column('enabled', sa.SmallInteger, nullable=False, server_default='1',
                      comment='是否启用'),
            sa.Column('is_deleted', sa.SmallInteger, nullable=False, server_default='0',
                      comment='软删标记'),
            sa.Column('create_user', sa.Integer, nullable=True, comment='最后修改人ID'),
            sa.Column('create_time', sa.DateTime, nullable=False,
                      server_default=sa.text('CURRENT_TIMESTAMP')),
            sa.Column('update_time', sa.DateTime, nullable=False,
                      server_default=update_time_server_default(conn)),
        )
    if not index_exists(conn, _TEMPLATE_TABLE, 'ix_doc_extract_template_tenant_id'):
        op.create_index('ix_doc_extract_template_tenant_id', _TEMPLATE_TABLE, ['tenant_id'])
    if not index_exists(conn, _TEMPLATE_TABLE, 'ix_doc_extract_template_doc_type'):
        op.create_index('ix_doc_extract_template_doc_type', _TEMPLATE_TABLE, ['doc_type'])

    if not table_exists(conn, _RUN_TABLE):
        op.create_table(
            _RUN_TABLE,
            sa.Column('id', sa.BigInteger, primary_key=True, autoincrement=True),
            sa.Column('tenant_id', sa.Integer, nullable=False, comment='租户ID'),
            sa.Column('template_id', sa.Integer, nullable=False,
                      comment='所用模板ID（可能是软删行）'),
            sa.Column('doc_type', sa.String(64), nullable=False,
                      comment='文档类型标识；冗余，模板改名/删除后仍可追溯'),
            sa.Column('determined_by', sa.String(16), nullable=False,
                      comment='类型确定方式：explicit / auto'),
            sa.Column('confidence', sa.Float, nullable=True,
                      comment='判定置信度；explicit 时为 NULL'),
            sa.Column('preprocess_route', sa.String(16), nullable=True,
                      comment='预处理链路：text / ocr'),
            sa.Column('fields', JsonType, nullable=True, comment='抽取出的结构化字段'),
            sa.Column('markdown', LargeText, nullable=False,
                      server_default='', comment='渲染结果'),
            sa.Column('status', sa.SmallInteger, nullable=False, server_default='1',
                      comment='1=成功 0=失败'),
            sa.Column('error_code', sa.Integer, nullable=True, comment='失败错误码'),
            sa.Column('error_message', sa.String(500), nullable=True, comment='失败原因'),
            sa.Column('user_id', sa.Integer, nullable=True, comment='发起人ID'),
            sa.Column('create_time', sa.DateTime, nullable=False,
                      server_default=sa.text('CURRENT_TIMESTAMP')),
        )
    if not index_exists(conn, _RUN_TABLE, 'ix_doc_extract_run_tenant_id'):
        op.create_index('ix_doc_extract_run_tenant_id', _RUN_TABLE, ['tenant_id'])
    if not index_exists(conn, _RUN_TABLE, 'ix_doc_extract_run_template_id'):
        op.create_index('ix_doc_extract_run_template_id', _RUN_TABLE, ['template_id'])


def downgrade() -> None:
    """Drop both tables and their indexes.

    Not lossy in the usual sense -- both tables are new in this revision and
    nothing else references them -- but dropping `doc_extract_run` discards the
    extraction history, which cannot be reconstructed from anywhere else.
    Confirm that is acceptable before rolling back.
    """
    conn = op.get_bind()

    if index_exists(conn, _RUN_TABLE, 'ix_doc_extract_run_template_id'):
        op.drop_index('ix_doc_extract_run_template_id', table_name=_RUN_TABLE)
    if index_exists(conn, _RUN_TABLE, 'ix_doc_extract_run_tenant_id'):
        op.drop_index('ix_doc_extract_run_tenant_id', table_name=_RUN_TABLE)
    if table_exists(conn, _RUN_TABLE):
        op.drop_table(_RUN_TABLE)

    if index_exists(conn, _TEMPLATE_TABLE, 'ix_doc_extract_template_doc_type'):
        op.drop_index('ix_doc_extract_template_doc_type', table_name=_TEMPLATE_TABLE)
    if index_exists(conn, _TEMPLATE_TABLE, 'ix_doc_extract_template_tenant_id'):
        op.drop_index('ix_doc_extract_template_tenant_id', table_name=_TEMPLATE_TABLE)
    if table_exists(conn, _TEMPLATE_TABLE):
        op.drop_table(_TEMPLATE_TABLE)
