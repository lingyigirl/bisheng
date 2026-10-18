"""Cross-node bundle persistence, integrity and immutable publication contracts."""

import io
from concurrent.futures import ThreadPoolExecutor

import pytest

from bisheng.linsight.domain.services.skill_store import SkillStore


class MemoryObjects:
    bucket = "private-test"

    def __init__(self):
        self.objects = {}
        self.gets = 0
        self.fail_put = False

    def put_object_sync(self, *, bucket_name, object_name, file):
        if self.fail_put:
            raise ConnectionError("synthetic storage failure")
        self.objects[object_name] = file

    def get_object_size_sync(self, *, bucket_name, object_name):
        if object_name not in self.objects:
            raise FileNotFoundError("missing test object")
        return len(self.objects[object_name])

    def download_object_sync(self, *, bucket_name, object_name):
        self.gets += 1
        if object_name not in self.objects:
            raise FileNotFoundError("missing test object")
        stream = io.BytesIO(self.objects[object_name])
        stream.release_conn = lambda: None
        return stream


@pytest.fixture
def nodes(tmp_path):
    objects = MemoryObjects()
    return SkillStore(root=tmp_path / "a", minio=objects), SkillStore(root=tmp_path / "b", minio=objects), objects


def test_cross_node_binary_content_and_cache_rebuild(nodes):
    a, b, objects = nodes
    files = {"SKILL.md": b"body", "assets/\u6a21\u677f.bin": bytes(range(256))}
    ref = a.write_bundle(1, "demo", files)
    assert b.load_bundle(1, "demo", ref.content_hash) == files
    before = objects.gets
    assert b.load_bundle(1, "demo", ref.content_hash) == files
    assert objects.gets == before
    b.invalidate_cache(1, "demo", ref.content_hash)
    assert b.load_bundle(1, "demo", ref.content_hash) == files
    assert objects.gets == before + 1


def test_corrupted_attachment_repaired_from_authority(nodes):
    a, b, _ = nodes
    ref = a.write_bundle(1, "demo", {"SKILL.md": b"body", "a.bin": b"correct"})
    b.load_bundle(1, "demo", ref.content_hash)
    (b.cache_dir(1, "demo", ref.content_hash) / "a.bin").write_bytes(b"wrong")
    assert b.load_bundle(1, "demo", ref.content_hash)["a.bin"] == b"correct"


def test_immutable_versions_and_failed_publish(nodes):
    a, b, objects = nodes
    v1 = a.write_bundle(1, "demo", {"SKILL.md": b"one"})
    v2 = a.write_bundle(1, "demo", {"SKILL.md": b"two"})
    assert v1.content_hash != v2.content_hash
    assert b.load_bundle(1, "demo", v1.content_hash)["SKILL.md"] == b"one"
    objects.fail_put = True
    with pytest.raises(ConnectionError):
        a.write_bundle(1, "demo", {"SKILL.md": b"three"})
    assert a.load_bundle(1, "demo", v2.content_hash)["SKILL.md"] == b"two"


def test_concurrent_cold_reads_download_once(nodes):
    a, b, objects = nodes
    ref = a.write_bundle(1, "demo", {"SKILL.md": b"body"})
    before = objects.gets
    with ThreadPoolExecutor(max_workers=8) as pool:
        values = list(pool.map(lambda _: b.load_bundle(1, "demo", ref.content_hash), range(16)))
    assert all(value == {"SKILL.md": b"body"} for value in values)
    assert objects.gets == before + 1


@pytest.mark.parametrize("path", ["../outside", "/absolute", "C:/outside", "a/../b", "a//b", "AUX.txt", "a."])
def test_unsafe_paths_rejected_before_upload(nodes, path):
    a, _, objects = nodes
    with pytest.raises(ValueError):
        a.write_bundle(1, "demo", {"SKILL.md": b"body", path: b"bad"})
    assert not objects.objects


def test_no_tenant_fallback_and_corrupt_object_rejected(nodes):
    a, b, objects = nodes
    ref = a.write_bundle(1, "demo", {"SKILL.md": b"body"})
    with pytest.raises(FileNotFoundError):
        b.load_bundle(2, "demo", ref.content_hash)
    objects.objects[ref.object_key] = b"broken zip"
    with pytest.raises(ValueError):
        b.load_bundle(1, "demo", ref.content_hash)
