"""The scan index is identical for fresh metadata and incremental upgrades."""

from importlib import import_module
from unittest.mock import MagicMock

import pytest

from bisheng.linsight.domain.models.linsight_session_version import LinsightSessionVersion


@pytest.mark.parametrize("exists", [False, True])
def test_index_upgrade_and_downgrade_are_guarded(monkeypatch, exists):
    migration = import_module("bisheng.core.database.alembic.versions.v2_7_0_f074_recovery_scan_index")
    operations = MagicMock()
    monkeypatch.setattr(migration, "op", operations)
    monkeypatch.setattr(migration, "index_exists", lambda *args: exists)
    migration.upgrade()
    migration.downgrade()
    if exists:
        operations.create_index.assert_not_called()
        operations.drop_index.assert_called_once_with(migration._INDEX, table_name=migration._TABLE)
    else:
        operations.drop_index.assert_not_called()
        operations.create_index.assert_called_once_with(migration._INDEX, migration._TABLE, ["status", "id"])
    index = next(index for index in LinsightSessionVersion.__table__.indexes if index.name == migration._INDEX)
    assert [column.name for column in index.columns] == ["status", "id"]
