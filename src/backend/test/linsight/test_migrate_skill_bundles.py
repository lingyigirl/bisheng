"""Explicit, restartable migration never invents missing source content."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from bisheng.linsight.domain.services.skill_store import SkillStore
from scripts.migrate_skill_bundles import migrate_one
from test.linsight.test_skill_object_storage import MemoryObjects


@pytest.fixture
def setup(tmp_path):
    store = SkillStore(root=tmp_path / "cache", legacy_root=tmp_path / "legacy", minio=MemoryObjects())
    base = store.skill_dir(1, "demo")
    base.mkdir(parents=True)
    (base / "SKILL.md").write_bytes(b"synthetic body")
    row = SimpleNamespace(id=1, tenant_id=1, name="demo", object_path="data/skills/1/demo", content_hash=None)
    repo = SimpleNamespace(publish=AsyncMock(return_value=True))
    return store, row, repo


async def test_preview_has_no_writes_and_apply_keeps_source(setup):
    store, row, repo = setup
    assert (await migrate_one(repo, store, row, False))["status"] == "pending"
    assert not store.minio.objects
    repo.publish.assert_not_awaited()
    assert (await migrate_one(repo, store, row, True))["status"] == "migrated"
    assert (store.skill_dir(1, "demo") / "SKILL.md").read_bytes() == b"synthetic body"
    values = repo.publish.call_args.args[1]
    row.content_hash, row.object_path = values["content_hash"], values["object_path"]
    assert (await migrate_one(repo, store, row, True))["status"] == "already_done"


async def test_missing_or_failed_source_never_updates_row(setup):
    store, row, repo = setup
    row.name = "missing"
    result = await migrate_one(repo, store, row, True)
    assert result["status"] == "missing"
    repo.publish.assert_not_awaited()


async def test_conflict_does_not_overwrite_new_metadata(setup):
    store, row, repo = setup
    repo.publish.return_value = False
    assert (await migrate_one(repo, store, row, True))["status"] == "conflict"
    assert row.content_hash is None


async def test_failed_readback_never_publishes(setup, monkeypatch):
    store, row, repo = setup
    monkeypatch.setattr(store, "_download", lambda *a: (_ for _ in ()).throw(ConnectionError("synthetic")))
    assert (await migrate_one(repo, store, row, True))["status"] == "failed"
    repo.publish.assert_not_awaited()


async def test_restore_requires_explicit_apply(setup, tmp_path):
    store, row, repo = setup
    ref = store.write_bundle(1, "demo", store.load_bundle(1, "demo"))
    row.content_hash, row.object_path = ref.content_hash, ref.object_key
    output = tmp_path / "restore"
    assert (await migrate_one(repo, store, row, False, output))["status"] == "pending_restore"
    assert not output.exists()
    assert (await migrate_one(repo, store, row, True, output))["status"] == "restored"
    assert (output / "data/skills/1/demo/SKILL.md").read_bytes() == b"synthetic body"
    assert repo.publish.call_args.args[1]["content_hash"] is None
