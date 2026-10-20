"""Opt-in F074 DDL and JSON round-trip against a dedicated, disposable DM8 user.

Set F074_DM8_URL to a dm+dmPython URL whose username starts with F074_.
The schema must be empty; this suite never targets the application's database.
"""

import importlib
import os

import pytest
import sqlalchemy as sa
from alembic.ddl.impl import DefaultImpl
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine

from bisheng.core.database.dialect_helpers import JsonType


@pytest.fixture(scope="module")
def dm_engine():
    value = os.getenv("F074_DM8_URL")
    if not value:
        pytest.skip("Dedicated DM8 test schema not configured")
    url = make_url(value)
    assert url.drivername == "dm+dmPython"
    assert (url.username or "").upper().startswith("F074_")
    engine = sa.create_engine(url)
    assert not sa.inspect(engine).get_table_names(), "Use a fresh disposable schema"

    # Match env.py's DM registration for this add-column-only migration; do not
    # execute env.py, which initializes every application model and migration.
    class DmColumnMigrationImpl(DefaultImpl):
        __dialect__ = "dm"
        transactional_ddl = False

    yield engine
    engine.dispose()


async def test_migration_and_configuration_round_trip(dm_engine):
    migration = importlib.import_module("bisheng.core.database.alembic.versions.v2_7_0_f074_agent_config")
    recovery = importlib.import_module("bisheng.core.database.alembic.versions.v2_7_0_f074_recovery")
    metadata = sa.MetaData()
    legacy = sa.Table(
        "linsight_session_version",
        metadata,
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.Integer, nullable=False),
    )
    metadata.create_all(dm_engine)
    with dm_engine.begin() as connection:
        connection.execute(legacy.insert().values(id="legacy", tenant_id=1))
        with Operations.context(MigrationContext.configure(connection)):
            migration.upgrade()
            migration.upgrade()
            recovery.upgrade()
            recovery.upgrade()
    columns = sa.inspect(dm_engine).get_columns("linsight_session_version")
    config_column = next(column for column in columns if column["name"].lower() == "agent_config")
    # dmSQLAlchemy reflects its CLOB-compatible storage as _DMText on DM8.
    assert str(config_column["type"]).upper() in {"CLOB", "TEXT"}
    assert config_column["nullable"]

    table = sa.Table(
        "linsight_session_version",
        sa.MetaData(),
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.Integer),
        sa.Column("agent_config", JsonType()),
        sa.Column("recovery_epoch", sa.Integer),
        sa.Column("recovery_state", JsonType()),
    )
    config = {
        "schema_version": 1,
        "event_protocol": 2,
        "main_model_id": "1",
        "subagents": [
            {
                "id": "analyst",
                "name": "分析员",
                "description": "日本語 English 中文",
                "system_prompt": "核对输入与输出。" * 1400,
                "model_id": "2",
                "skill_names": ["sales-analysis"],
                "tool_refs": [6],
                "connector_refs": [39],
            }
        ],
    }
    with dm_engine.begin() as connection:
        assert connection.execute(sa.select(table.c.agent_config).where(table.c.id == "legacy")).scalar() is None
        assert connection.execute(sa.select(table.c.recovery_epoch).where(table.c.id == "legacy")).scalar_one() == 0
        connection.execute(
            table.insert(),
            [
                {"id": "configured", "tenant_id": 1, "agent_config": config},
                {"id": "other-tenant", "tenant_id": 2, "agent_config": {"subagents": []}},
            ],
        )
    with dm_engine.connect() as connection:
        restored = connection.execute(sa.select(table.c.agent_config).where(table.c.id == "configured")).scalar_one()
        assert restored == config
    async_engine = create_async_engine(dm_engine.url.set(drivername="dm+dmAsync"))
    try:
        async with async_engine.connect() as connection:
            result = await connection.execute(sa.select(table.c.agent_config).where(table.c.id == "configured"))
            assert result.scalar_one() == config
    finally:
        await async_engine.dispose()
    with dm_engine.begin() as connection:
        connection.execute(
            table.update()
            .where(table.c.id == "configured")
            .values(
                agent_config={"subagents": []},
                recovery_epoch=2,
                recovery_state={"attempts": 1, "workspace_epoch": 2, "phase": "recovering"},
            )
        )
    with dm_engine.connect() as connection:
        assert connection.execute(sa.select(table.c.agent_config).where(table.c.id == "configured")).scalar_one() == {
            "subagents": []
        }
        assert connection.execute(sa.select(table.c.agent_config).where(table.c.id == "other-tenant")).scalar_one() == {
            "subagents": []
        }
        # Old application code can still read rows without knowing the new column.
        assert len(connection.execute(sa.select(legacy.c.id)).all()) == 3
        row = connection.execute(
            sa.select(table.c.recovery_epoch, table.c.recovery_state).where(table.c.id == "configured")
        ).one()
        assert row.recovery_epoch == 2
        assert row.recovery_state == {"attempts": 1, "workspace_epoch": 2, "phase": "recovering"}
