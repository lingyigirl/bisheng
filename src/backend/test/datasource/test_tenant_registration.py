"""Guard: F061's four tables must stay in the tenant-aware module list.

Why this is a test and not a comment:

``bisheng.core.database.tenant_filter._TENANT_AWARE_MODEL_MODULES`` drives two
ORM hooks — ``do_orm_execute`` (inject ``WHERE tenant_id = <current>`` on
SELECT) and ``before_flush`` (auto-fill ``tenant_id`` on INSERT). Both are
resolved from that list, and the discovery runs ONCE at startup.

For an unregistered table the failure is silent and twofold: reads return other
tenants' rows, AND inserts omit tenant_id so rows land in the root tenant. That
second half is why this matters more here than elsewhere — an unregistered
``db_query_log`` would pool every tenant's query audit trail into one place.

The list is a tuple of *strings*, so nothing about the models themselves keeps
them registered: a rename of the module path, or a refactor that drops the
import edge that once pulled them onto the router chain, breaks it with no
error anywhere. Hence a direct assertion on the strings.
"""

import pytest

from bisheng.core.database.tenant_filter import _TENANT_AWARE_MODEL_MODULES

# The four F061 tables, as they appear in the registration list.
F061_MODULES = (
    'bisheng.datasource.domain.models.datasource',
    'bisheng.datasource.domain.models.datasource_table',
    'bisheng.datasource.domain.models.query_log',
    'bisheng.datasource.domain.models.query_failure',
)


@pytest.mark.parametrize('module_path', F061_MODULES)
def test_f061_table_is_registered_as_tenant_aware(module_path):
    assert module_path in _TENANT_AWARE_MODEL_MODULES, (
        f'{module_path} is missing from _TENANT_AWARE_MODEL_MODULES. Without it '
        'the table is neither tenant-filtered on read nor auto-filled on insert.'
    )


@pytest.mark.parametrize('module_path', F061_MODULES)
def test_registered_module_path_actually_imports(module_path):
    """A registration that names a module which no longer exists is dead weight:
    the discovery would import nothing and the table would silently go unfiltered
    while this list still looks correct."""
    __import__(module_path)


def test_every_f061_model_declares_a_tenant_id_column():
    """The filter injects `WHERE tenant_id = ...`; a model without the column
    makes that predicate raise rather than filter."""
    from bisheng.datasource.domain.models import (
        DbDatasource,
        DbDatasourceTable,
        DbQueryFailure,
        DbQueryLog,
    )

    for model in (DbDatasource, DbDatasourceTable, DbQueryLog, DbQueryFailure):
        assert 'tenant_id' in model.__table__.columns, (
            f'{model.__tablename__} has no tenant_id column but is registered '
            'as tenant-aware'
        )


def test_datasource_models_are_not_knowledge_types():
    """spec AC-61: datasources must NOT enter the knowledge type enum. Reusing
    knowledge plumbing is the tempting shortcut, and it would drag the
    feature into vector stores and collection creation."""
    from bisheng.knowledge.domain.models.knowledge import KnowledgeTypeEnum

    values = {member.value for member in KnowledgeTypeEnum}
    assert values == {0, 1, 2, 3}, (
        f'KnowledgeTypeEnum changed to {sorted(values)}; datasource work must '
        'not add a member here (spec AC-61 / AC-64)'
    )


# --- the ORM and the migration must agree on the assistant binding -----------


def test_assistant_link_declares_the_datasource_column():
    """The migration adds `assistantlink.datasource_id`, but that alone puts the
    column in the DATABASE and not in the ORM -- SQLModel would neither read nor
    write it, and nothing fails loudly.

    That is exactly what happened: the migration (with a paragraph explaining
    why the column must exist) shipped two commits before the model field did.
    """
    from bisheng.database.models.assistant import AssistantLink

    assert 'datasource_id' in AssistantLink.__table__.columns, (
        'the assistant binding column is missing from the ORM; the migration '
        'creates it but SQLModel cannot use it'
    )


def test_the_migration_creates_the_index_the_orm_declares():
    """Both paths must converge.

    `create_all` builds indexes from the model's `index=True`; the migration
    has to build the same one by hand. When they diverge, a fresh install and an
    upgraded one end up with different schemas and nothing reports it.
    """
    from pathlib import Path

    from bisheng.database.models.assistant import AssistantLink

    declared = {i.name for i in AssistantLink.__table__.indexes}

    migration = (
        Path(__file__).resolve().parents[2]
        / 'bisheng/core/database/alembic/versions/v2_7_0_f061_datasource.py'
    ).read_text(encoding='utf-8')

    for index_name in declared:
        if 'datasource' not in index_name:
            continue
        assert index_name in migration, (
            f'the ORM declares {index_name} but the migration never creates it '
            '-- a migrated database would lack an index a fresh one has'
        )
