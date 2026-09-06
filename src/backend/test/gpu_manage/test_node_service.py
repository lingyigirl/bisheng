"""Unit tests for GPU node service: parsing, path guards, password hygiene."""

import pytest

from bisheng.common.errcode.gpu_manage import (
    GpuNodeExistsError,
    GpuNodePasswordRequiredError,
    InvalidBrowsePathError,
)
from bisheng.gpu_manage.domain.models import gpu_node as gpu_node_model
from bisheng.gpu_manage.domain.models.gpu_node import GpuNode, GpuNodeCreate
from bisheng.gpu_manage.domain.services import node_service as node_service_module
from bisheng.gpu_manage.domain.services.node_service import NodeService
from bisheng.gpu_manage.domain.services.ssh_executor import SSHResult

GPU_STATS_OUT = (
    "0, NVIDIA GeForce RTX 4090, 49152, 17110, 32042, 35, 62\n1, NVIDIA GeForce RTX 4090, 49152, 45388, 3764, 92, 71\n"
)

DOCKER_PS_OUT = (
    "abc123\tqwen3-asr\tvllm-openai-qwen3asr:local\tUp 7 hours\t0.0.0.0:8020->8000/tcp\n"
    "def456\tone-api\toneapi:latest\tUp 7 days\t0.0.0.0:3001->3000/tcp\n"
)

LS_OUT = "total 8\ndrwxr-xr-x 2 root root 4096 Aug 20 10:00 models\n-rw-r--r-- 1 root root 12 Aug 20 10:00 readme.txt\n"


class _FakeDao:
    def __init__(self):
        self.nodes: dict[int, GpuNode] = {}

    async def find_by_id(self, node_id):
        return self.nodes.get(node_id)

    async def find_by_host(self, host):
        return next((n for n in self.nodes.values() if n.host == host), None)

    async def find_all(self):
        return list(self.nodes.values())

    async def insert(self, node):
        node.id = len(self.nodes) + 1
        self.nodes[node.id] = node
        return node

    async def update(self, node):
        self.nodes[node.id] = node
        return node

    async def delete(self, node_id):
        self.nodes.pop(node_id, None)


def _make_node(host="172.19.0.3", model_dir="/data1") -> GpuNode:
    node = GpuNode(name="GPU-01", host=host, port=22, username="root", model_dir=model_dir)
    node.password_encrypted = gpu_node_model.encrypt_password("secret")
    return node


async def test_create_node_encrypts_password(monkeypatch):
    dao = _FakeDao()
    monkeypatch.setattr(node_service_module, "GpuNodeDao", dao)

    payload = GpuNodeCreate(name="GPU-01", host="10.0.0.1", password="secret", model_dir="/data1")
    read = await NodeService.create_node(payload)

    assert read.has_password is True
    stored = dao.nodes[read.id]
    assert "secret" not in stored.password_encrypted
    assert stored.decrypt_password() == "secret"
    # response never carries the password
    assert not hasattr(read, "password")
    assert not hasattr(read, "password_encrypted")


async def test_create_node_requires_password(monkeypatch):
    dao = _FakeDao()
    monkeypatch.setattr(node_service_module, "GpuNodeDao", dao)
    with pytest.raises(GpuNodePasswordRequiredError):
        await NodeService.create_node(GpuNodeCreate(name="x", host="10.0.0.1", password="", model_dir="/data1"))


async def test_create_node_rejects_duplicate_host(monkeypatch):
    dao = _FakeDao()
    dao.nodes[1] = _make_node()
    monkeypatch.setattr(node_service_module, "GpuNodeDao", dao)
    with pytest.raises(GpuNodeExistsError):
        await NodeService.create_node(GpuNodeCreate(name="dup", host="172.19.0.3", password="x"))


class _NodeDaoStub:
    def __init__(self, node: GpuNode):
        self.node = node

    async def find_by_id(self, node_id):
        return self.node


async def test_list_gpus_parses_csv(monkeypatch):
    node = _make_node()
    monkeypatch.setattr(node_service_module, "GpuNodeDao", _NodeDaoStub(node))

    async def fake_run_remote(_, command):
        return SSHResult(0, GPU_STATS_OUT, "")

    monkeypatch.setattr(NodeService, "_run_remote", fake_run_remote)
    gpus = await NodeService.list_gpus(1)
    assert len(gpus) == 2
    assert gpus[0].index == 0
    assert gpus[0].memory_total_mb == 49152
    assert gpus[1].util_percent == 92


async def test_list_containers_parses_docker_ps(monkeypatch):
    monkeypatch.setattr(node_service_module, "GpuNodeDao", _NodeDaoStub(_make_node()))

    async def fake_run_remote(_, command):
        return SSHResult(0, DOCKER_PS_OUT, "")

    monkeypatch.setattr(NodeService, "_run_remote", fake_run_remote)
    containers = await NodeService.list_containers(1)
    assert len(containers) == 2
    assert containers[0].name == "qwen3-asr"
    assert containers[0].status == "Up 7 hours"


async def test_list_model_files_parses_ls(monkeypatch):
    monkeypatch.setattr(node_service_module, "GpuNodeDao", _NodeDaoStub(_make_node()))

    async def fake_run_remote(_, command):
        if command.startswith("ls"):
            return SSHResult(0, LS_OUT, "")
        return SSHResult(0, "4096\t/path\n", "")

    monkeypatch.setattr(NodeService, "_run_remote", fake_run_remote)
    files = await NodeService.list_model_files(1, "")
    assert [f.name for f in files] == ["models", "readme.txt"]
    assert files[0].is_dir is True
    assert files[1].size_bytes == 12


async def test_file_browse_rejects_path_escape(monkeypatch):
    monkeypatch.setattr(node_service_module, "GpuNodeDao", _NodeDaoStub(_make_node()))

    async def fake_run_remote(_, command):
        raise AssertionError("remote command must not run")

    monkeypatch.setattr(NodeService, "_run_remote", fake_run_remote)
    with pytest.raises(InvalidBrowsePathError):
        await NodeService.list_model_files(1, "../../etc")
    with pytest.raises(InvalidBrowsePathError):
        await NodeService.delete_model_file(1, "/etc/passwd")
    with pytest.raises(InvalidBrowsePathError):
        await NodeService.delete_model_file(1, "/data1")
