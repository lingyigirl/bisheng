"""Both fresh metadata and existing-table upgrades support nullable versions."""

from importlib import import_module
from unittest.mock import MagicMock

import pytest

from bisheng.linsight.domain.models.linsight_skill import LinsightSkill


@pytest.mark.parametrize("exists", [True, False])
def test_hash_column_ddl_is_guarded(monkeypatch, exists):
    migration = import_module("bisheng.core.database.alembic.versions.v2_7_0_f035_skill_object_storage")
    operations = MagicMock()
    monkeypatch.setattr(migration, "op", operations)
    monkeypatch.setattr(migration, "table_exists", lambda table: True)
    monkeypatch.setattr(migration, "column_exists", lambda table, column: exists)
    migration.upgrade()
    migration.downgrade()
    if exists:
        operations.add_column.assert_not_called()
        operations.drop_column.assert_called_once_with("linsight_skill", "content_hash")
    else:
        operations.drop_column.assert_not_called()
        assert operations.add_column.call_args.args[1].name == "content_hash"
    column = LinsightSkill.__table__.c.content_hash
    assert column.nullable and column.type.length == 64
