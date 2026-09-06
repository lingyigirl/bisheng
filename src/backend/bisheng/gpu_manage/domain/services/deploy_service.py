"""Deploy template/instance management: docker containers on GPU nodes over SSH."""

import json
import os

from loguru import logger

from bisheng.common.errcode.gpu_manage import (
    DeployInstanceNotFoundError,
    DeployOperationError,
    DeployRunError,
    DeployTemplateNotFoundError,
    InvalidContainerNameError,
    InvalidGpuIndexError,
    InvalidModelPathError,
)
from bisheng.common.services.config_service import settings
from bisheng.gpu_manage.domain.models.deploy_instance import (
    DeployInstance,
    DeployInstanceCreate,
    DeployInstanceDao,
)
from bisheng.gpu_manage.domain.models.deploy_template import (
    DeployTemplate,
    DeployTemplateCreate,
    DeployTemplateDao,
    DeployTemplateUpdate,
)
from bisheng.gpu_manage.domain.models.gpu_node import GpuNode, GpuNodeDao
from bisheng.gpu_manage.domain.seed_data import ensure_builtin_templates
from bisheng.gpu_manage.domain.services import commands
from bisheng.gpu_manage.domain.services.ssh_executor import SSHResult, SSHSession, run_ssh


def _session_for(node: GpuNode) -> SSHSession:
    return SSHSession(node.host, node.port, node.username, node.decrypt_password())


async def _run_remote(node: GpuNode, command: str, fail_msg: str = "container operation failed") -> SSHResult:
    from bisheng.common.errcode.gpu_manage import GpuNodeConnectError

    try:
        result = await run_ssh(_session_for(node), command)
    except Exception as exc:
        raise GpuNodeConnectError(msg=str(exc)) from exc
    if result.exit_code != 0:
        logger.warning("{}: {} -> exit {} stderr={}", fail_msg, command[:160], result.exit_code, result.stderr[:400])
        raise DeployOperationError(msg=f"{fail_msg}: {result.stderr[:400] or result.stdout[:200]}")
    return result


async def _inspect_status(node: GpuNode, name: str) -> str:
    """Live container status: running|stopped|error|missing."""
    result = await run_ssh(_session_for(node), commands.DOCKER_INSPECT_STATUS_CMD.format(name=name))
    if result.exit_code != 0:
        return "missing"
    status = result.stdout.strip()
    return status if status in ("running", "stopped", "error", "created", "exited") else "unknown"


def _render_start_args(template: DeployTemplate) -> str:
    if not template.start_args:
        return ""
    try:
        pairs = json.loads(template.start_args)
    except json.JSONDecodeError as exc:
        raise DeployRunError(msg=f"start_args is not valid JSON: {exc}") from exc
    if not isinstance(pairs, dict):
        raise DeployRunError(msg="start_args must be a JSON object")
    rendered = []
    for key, value in pairs.items():
        if not commands.START_ARG_KEY_RE.match(key) or not commands.validate_safe_value(str(value)):
            raise DeployRunError(msg=f"unsafe start argument: {key}")
        rendered.append(f"{key} {value}")
    return " ".join(rendered)


async def _resolve_node_and_template(node_id: int, template_id: int) -> tuple[GpuNode, DeployTemplate]:
    node = await GpuNodeDao.find_by_id(node_id)
    if not node:
        from bisheng.common.errcode.gpu_manage import GpuNodeNotFoundError

        raise GpuNodeNotFoundError()
    template = await DeployTemplateDao.find_by_id(template_id)
    if not template:
        raise DeployTemplateNotFoundError()
    if os.path.commonpath([template.model_path, node.model_dir]) != node.model_dir:
        raise InvalidModelPathError(msg=f"model path must be under {node.model_dir}")
    if not commands.validate_gpu_index(template.gpu_index):
        raise InvalidGpuIndexError()
    if not commands.validate_port(template.host_port) or not commands.validate_port(template.container_port):
        raise DeployRunError(msg="invalid port")
    return node, template


class DeployService:
    @staticmethod
    async def list_templates() -> list[DeployTemplate]:
        templates = await DeployTemplateDao.find_all()
        if not templates:
            return await ensure_builtin_templates(templates)
        return templates

    @staticmethod
    async def create_template(payload: DeployTemplateCreate) -> DeployTemplate:
        template = DeployTemplate(**payload.model_dump())
        return await DeployTemplateDao.insert(template)

    @staticmethod
    async def update_template(template_id: int, payload: DeployTemplateUpdate) -> DeployTemplate:
        template = await DeployTemplateDao.find_by_id(template_id)
        if not template:
            raise DeployTemplateNotFoundError()
        for key, value in payload.model_dump(exclude_unset=True).items():
            setattr(template, key, value)
        return await DeployTemplateDao.update(template)

    @staticmethod
    async def delete_template(template_id: int) -> None:
        if not await DeployTemplateDao.find_by_id(template_id):
            raise DeployTemplateNotFoundError()
        await DeployTemplateDao.delete(template_id)

    @staticmethod
    async def create_instance(payload: DeployInstanceCreate) -> DeployInstance:
        if not commands.validate_container_name(payload.name):
            raise InvalidContainerNameError()
        node, template = await _resolve_node_and_template(payload.node_id, payload.template_id)

        existing = await DeployInstanceDao.find_by_node_and_name(node.id, payload.name)
        if existing:
            return existing

        status = await _inspect_status(node, payload.name)
        if status == "missing":
            # Fresh deployment
            rendered_args = _render_start_args(template)
            command = commands.DOCKER_RUN_TMPL.format(
                name=payload.name,
                gpu_index=template.gpu_index,
                host_port=template.host_port,
                container_port=template.container_port,
                model_path=template.model_path,
                image=template.image,
                served_model_name=template.served_model_name,
                args=rendered_args,
            )
            result = await run_ssh(_session_for(node), command)
            if result.exit_code != 0:
                raise DeployRunError(msg=result.stderr[:500] or result.stdout[:200])
            status = await _inspect_status(node, payload.name)
        else:
            # Adopt an existing container (e.g. the manually deployed qwen3-asr)
            logger.info("adopting existing container {} on node {}", payload.name, node.id)

        instance = DeployInstance(
            node_id=node.id,
            template_id=template.id,
            name=payload.name,
            status=status,
            host_port=template.host_port,
        )
        return await DeployInstanceDao.insert(instance)

    @staticmethod
    async def list_instances(node_id: int | None = None) -> list[DeployInstance]:
        return await DeployInstanceDao.find_all(node_id)

    @staticmethod
    async def _require_instance(instance_id: int) -> tuple[DeployInstance, GpuNode]:
        instance = await DeployInstanceDao.find_by_id(instance_id)
        if not instance:
            raise DeployInstanceNotFoundError()
        node = await GpuNodeDao.find_by_id(instance.node_id)
        if not node:
            raise DeployInstanceNotFoundError(msg="instance node is gone")
        return instance, node

    @staticmethod
    async def _apply(instance_id: int, command_tmpl: str, fail_msg: str) -> str:
        instance, node = await DeployService._require_instance(instance_id)
        command = command_tmpl.format(name=instance.name)
        await _run_remote(node, command, fail_msg)
        status = await _inspect_status(node, instance.name)
        instance.status = status
        await DeployInstanceDao.update(instance)
        return status

    @staticmethod
    async def start_instance(instance_id: int) -> str:
        return await DeployService._apply(instance_id, commands.DOCKER_START_TMPL, "docker start failed")

    @staticmethod
    async def stop_instance(instance_id: int) -> str:
        return await DeployService._apply(instance_id, commands.DOCKER_STOP_TMPL, "docker stop failed")

    @staticmethod
    async def restart_instance(instance_id: int) -> str:
        return await DeployService._apply(instance_id, commands.DOCKER_RESTART_TMPL, "docker restart failed")

    @staticmethod
    async def get_status(instance_id: int) -> str:
        instance, node = await DeployService._require_instance(instance_id)
        status = await _inspect_status(node, instance.name)
        instance.status = status
        await DeployInstanceDao.update(instance)
        return status

    @staticmethod
    async def get_logs(instance_id: int, tail: int | None = None) -> str:
        instance, node = await DeployService._require_instance(instance_id)
        tail_lines = tail or settings.gpu_manage.log_tail_lines
        command = commands.DOCKER_LOGS_TMPL.format(tail=tail_lines, name=instance.name)
        result = await run_ssh(_session_for(node), command)
        if result.exit_code != 0:
            return ""
        return result.stdout + result.stderr

    @staticmethod
    async def delete_instance(instance_id: int) -> None:
        instance, node = await DeployService._require_instance(instance_id)
        # Container removal is tolerated when the container is already gone.
        result = await run_ssh(_session_for(node), commands.DOCKER_RM_TMPL.format(name=instance.name))
        if result.exit_code != 0 and "No such container" not in result.stderr:
            raise DeployOperationError(msg=result.stderr[:400])
        await DeployInstanceDao.delete(instance.id)
