"""Unit tests for deploy service: validation, run/adopt decision, command rendering."""

import pytest

from bisheng.common.errcode.gpu_manage import (
    DeployRunError,
    InvalidContainerNameError,
    InvalidGpuIndexError,
    InvalidModelPathError,
)
from bisheng.gpu_manage.domain.models.deploy_instance import DeployInstanceCreate
from bisheng.gpu_manage.domain.models.deploy_template import DeployTemplate
from bisheng.gpu_manage.domain.services import deploy_service
from bisheng.gpu_manage.domain.services.deploy_service import DeployService, _render_start_args
from bisheng.gpu_manage.domain.services.ssh_executor import SSHResult


def _template(**overrides) -> DeployTemplate:
    data = {
        "id": 1,
        "name": "T",
        "image": "vllm/vllm-openai:latest",
        "model_path": "/data1/models/Qwen3-8B",
        "gpu_index": "0",
        "host_port": 8001,
        "container_port": 8000,
        "served_model_name": "Qwen3-8B",
        "start_args": '{"--gpu-memory-utilization": 0.9}',
        "is_builtin": False,
    }
    data.update(overrides)
    return DeployTemplate(**data)


def test_render_start_args_safe():
    assert _render_start_args(_template()) == "--gpu-memory-utilization 0.9"


def test_render_start_args_rejects_shell_metachars():
    with pytest.raises(DeployRunError):
        _render_start_args(_template(start_args='{"--x": "a; rm -rf /"}'))


def test_render_start_args_rejects_bad_key():
    with pytest.raises(DeployRunError):
        _render_start_args(_template(start_args='{"badkey": "1"}'))


def test_render_start_args_rejects_bad_json():
    with pytest.raises(DeployRunError):
        _render_start_args(_template(start_args="not json"))


async def test_create_instance_rejects_bad_container_name():
    with pytest.raises(InvalidContainerNameError):
        await DeployService.create_instance(DeployInstanceCreate(node_id=1, template_id=1, name="a;rm -rf /"))


def _node_stub(model_dir="/data1"):
    from bisheng.gpu_manage.domain.models.gpu_node import GpuNode, encrypt_password

    node = GpuNode(name="n", host="10.0.0.1", model_dir=model_dir)
    node.password_encrypted = encrypt_password("secret")
    return node


class _TemplateDaoStub:
    def __init__(self, template: DeployTemplate):
        self.template = template

    async def find_by_id(self, template_id):
        return self.template


class _InstanceDaoStub:
    def __init__(self):
        self.inserted = None

    async def find_by_node_and_name(self, node_id, name):
        return None

    async def insert(self, instance):
        instance.id = 1
        self.inserted = instance
        return instance


async def test_create_instance_rejects_bad_model_path(monkeypatch):
    class _NodeDaoStub:
        async def find_by_id(self, node_id):
            return _node_stub()

    monkeypatch.setattr(deploy_service, "GpuNodeDao", _NodeDaoStub())
    monkeypatch.setattr(deploy_service, "DeployTemplateDao", _TemplateDaoStub(_template(model_path="/etc/models")))
    monkeypatch.setattr(deploy_service, "DeployInstanceDao", _InstanceDaoStub())
    with pytest.raises(InvalidModelPathError):
        await DeployService.create_instance(DeployInstanceCreate(node_id=1, template_id=1, name="bisheng-x"))


async def test_create_instance_rejects_bad_gpu_index(monkeypatch):
    class _NodeDaoStub:
        async def find_by_id(self, node_id):
            return _node_stub()

    monkeypatch.setattr(deploy_service, "GpuNodeDao", _NodeDaoStub())
    monkeypatch.setattr(deploy_service, "DeployTemplateDao", _TemplateDaoStub(_template(gpu_index="a;b")))
    with pytest.raises(InvalidGpuIndexError):
        await DeployService.create_instance(DeployInstanceCreate(node_id=1, template_id=1, name="bisheng-x"))


async def test_create_instance_adopts_existing_container(monkeypatch):
    class _NodeDaoStub:
        async def find_by_id(self, node_id):
            return _node_stub()

    instance_dao = _InstanceDaoStub()
    ran = []

    async def fake_run_ssh(session, command, timeout=None):
        ran.append(command)
        if "docker inspect" in command:
            return SSHResult(0, "running\n", "")
        return SSHResult(0, "", "")

    monkeypatch.setattr(deploy_service, "GpuNodeDao", _NodeDaoStub())
    monkeypatch.setattr(deploy_service, "DeployTemplateDao", _TemplateDaoStub(_template()))
    monkeypatch.setattr(deploy_service, "DeployInstanceDao", instance_dao)
    monkeypatch.setattr(deploy_service, "run_ssh", fake_run_ssh)

    instance = await DeployService.create_instance(DeployInstanceCreate(node_id=1, template_id=1, name="qwen3-asr"))
    assert instance.status == "running"
    # adopt path must not emit docker run
    assert not any(command.startswith("docker run") for command in ran)


async def test_create_instance_runs_when_missing(monkeypatch):
    class _NodeDaoStub:
        async def find_by_id(self, node_id):
            return _node_stub()

    ran = []

    async def fake_run_ssh(session, command, timeout=None):
        ran.append(command)
        if "docker inspect" in command:
            return SSHResult(1, "", "No such object")
        return SSHResult(0, "", "")

    monkeypatch.setattr(deploy_service, "GpuNodeDao", _NodeDaoStub())
    monkeypatch.setattr(deploy_service, "DeployTemplateDao", _TemplateDaoStub(_template()))
    monkeypatch.setattr(deploy_service, "DeployInstanceDao", _InstanceDaoStub())
    monkeypatch.setattr(deploy_service, "run_ssh", fake_run_ssh)

    instance = await DeployService.create_instance(DeployInstanceCreate(node_id=1, template_id=1, name="bisheng-new"))
    assert instance.status == "missing"
    run_commands = [c for c in ran if c.startswith("docker run")]
    assert len(run_commands) == 1
    assert "--gpu-memory-utilization 0.9" in run_commands[0]
    assert "--served-model-name Qwen3-8B" in run_commands[0]
