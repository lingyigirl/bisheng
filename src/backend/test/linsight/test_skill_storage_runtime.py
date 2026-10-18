"""Migration leaves recovery digests stable; missing selected bundles fail closed."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from bisheng.common.errcode.linsight import LinsightAgentConfigurationError
from bisheng.linsight.domain.services import skill_provisioning
from bisheng.linsight.domain.services.agent_config_service import skill_digest
from bisheng.linsight.domain.services.skill_store import SkillStore
from test.linsight.test_skill_object_storage import MemoryObjects


def test_recovery_digest_survives_storage_migration(tmp_path):
    store = SkillStore(root=tmp_path, minio=MemoryObjects())
    base = store.skill_dir(1, "demo")
    base.mkdir(parents=True)
    (base / "SKILL.md").write_bytes(b"synthetic body")
    old = skill_digest(store, 1, "demo")
    ref = store.write_bundle(1, "demo", store.load_bundle(1, "demo"))
    assert skill_digest(store, 1, "demo", ref.content_hash) == old
    changed = store.write_bundle(1, "demo", {"SKILL.md": b"changed"})
    assert skill_digest(store, 1, "demo", changed.content_hash) != old


async def test_selected_missing_bundle_is_not_silently_skipped(tmp_path, monkeypatch):
    monkeypatch.setattr(
        skill_provisioning.LinsightSkillDao,
        "list_enabled",
        AsyncMock(return_value=[SimpleNamespace(name="missing", content_hash=None)]),
    )
    backend = SimpleNamespace(aupload_files=AsyncMock())
    with pytest.raises(LinsightAgentConfigurationError):
        await skill_provisioning.materialize_session_skills(backend, 1, ["missing"], store=SkillStore(root=tmp_path))
    backend.aupload_files.assert_not_awaited()
