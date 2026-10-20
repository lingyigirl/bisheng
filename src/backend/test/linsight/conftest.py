"""Skill unit tests use isolated objects; no runtime MinIO access."""

import pytest

from test.linsight.test_skill_object_storage import MemoryObjects


@pytest.fixture(autouse=True)
def skill_objects(monkeypatch, request):
    if not any(
        request.node.path.name.startswith(p)
        for p in (
            "test_skill_",
            "test_github_skill_import",
            "test_migrate_sop_to_skill",
        )
    ):
        return
    from bisheng.linsight.domain.repositories.skill_repository import SkillRepository
    from bisheng.linsight.domain.services import skill_service, skill_store

    objects = MemoryObjects()
    monkeypatch.setattr(skill_store, "get_minio_storage_sync", lambda: objects)

    class FakeRepository(SkillRepository):
        async def publish(self, previous, values):
            current = self.dao.rows.get(previous.name)
            if (
                current is None
                or current.content_hash != previous.content_hash
                or current.object_path != previous.object_path
            ):
                return False
            self.dao.rows[previous.name] = current.model_copy(update=values)
            return True

    monkeypatch.setattr(
        skill_service,
        "SkillRepository",
        lambda dao: FakeRepository(dao=dao) if hasattr(dao, "rows") else SkillRepository(dao=dao),
    )
