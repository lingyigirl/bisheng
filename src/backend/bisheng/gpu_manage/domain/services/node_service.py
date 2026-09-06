"""GPU node CRUD and remote monitoring."""

import os

from loguru import logger

from bisheng.common.errcode.gpu_manage import (
    DeleteModelFileError,
    GpuNodeConnectError,
    GpuNodeExistsError,
    GpuNodeNotFoundError,
    GpuNodePasswordRequiredError,
    InvalidBrowsePathError,
)
from bisheng.gpu_manage.domain.models.gpu_node import (
    GpuNode,
    GpuNodeCreate,
    GpuNodeDao,
    GpuNodeRead,
    GpuNodeUpdate,
    encrypt_password,
    to_read,
)
from bisheng.gpu_manage.domain.schemas.monitor import ContainerInfo, GpuInfo, GpuProcess, ModelFileInfo
from bisheng.gpu_manage.domain.services import commands
from bisheng.gpu_manage.domain.services.ssh_executor import SSHSession, run_ssh


def _require_node(node: GpuNode | None) -> GpuNode:
    if not node:
        raise GpuNodeNotFoundError()
    return node


def _session_for(node: GpuNode) -> SSHSession:
    return SSHSession(node.host, node.port, node.username, node.decrypt_password())


class NodeService:
    @staticmethod
    async def create_node(payload: GpuNodeCreate) -> GpuNodeRead:
        if not payload.password:
            raise GpuNodePasswordRequiredError()
        if await GpuNodeDao.find_by_host(payload.host):
            raise GpuNodeExistsError()
        node = GpuNode(**payload.model_dump(exclude={"password"}))
        node.password_encrypted = encrypt_password(payload.password)
        node = await GpuNodeDao.insert(node)
        return to_read(node)

    @staticmethod
    async def update_node(node_id: int, payload: GpuNodeUpdate) -> GpuNodeRead:
        node = _require_node(await GpuNodeDao.find_by_id(node_id))
        updates = payload.model_dump(exclude_unset=True, exclude={"password"})
        if payload.password:
            node.password_encrypted = encrypt_password(payload.password)
        for key, value in updates.items():
            setattr(node, key, value)
        node = await GpuNodeDao.update(node)
        return to_read(node)

    @staticmethod
    async def delete_node(node_id: int) -> None:
        node = _require_node(await GpuNodeDao.find_by_id(node_id))
        # Deploy instances tracked in DB are removed too; remote containers are
        # left untouched (documented behavior).
        from bisheng.gpu_manage.domain.models.deploy_instance import DeployInstanceDao

        await DeployInstanceDao.delete_by_node(node_id)
        await GpuNodeDao.delete(node.id)

    @staticmethod
    async def list_nodes() -> list[GpuNodeRead]:
        return [to_read(node) for node in await GpuNodeDao.find_all()]

    @staticmethod
    async def get_node(node_id: int) -> GpuNodeRead:
        return to_read(_require_node(await GpuNodeDao.find_by_id(node_id)))

    @staticmethod
    async def test_connection(node_id: int) -> dict:
        node = _require_node(await GpuNodeDao.find_by_id(node_id))
        try:
            result = await run_ssh(_session_for(node), commands.TEST_CMD)
        except Exception as exc:
            logger.warning("GPU node {} connection test failed: {}", node.host, exc)
            return {"ok": False, "gpu_count": 0, "error": str(exc)}
        if result.exit_code != 0:
            return {"ok": False, "gpu_count": 0, "error": result.stderr or "nvidia-smi failed"}
        lines = [line for line in result.stdout.splitlines() if line.strip()]
        return {"ok": True, "gpu_count": len(lines), "error": None}

    @staticmethod
    async def _run_remote(node: GpuNode, command: str):
        try:
            result = await run_ssh(_session_for(node), command)
        except Exception as exc:
            raise GpuNodeConnectError(msg=str(exc)) from exc
        if result.exit_code != 0:
            raise GpuNodeConnectError(msg=result.stderr or f"exit {result.exit_code}")
        return result

    @staticmethod
    async def list_gpus(node_id: int) -> list[GpuInfo]:
        node = _require_node(await GpuNodeDao.find_by_id(node_id))
        result = await NodeService._run_remote(node, commands.GPU_STATS_CMD)
        gpus = []
        for line in result.stdout.splitlines():
            parts = [part.strip() for part in line.split(",")]
            if len(parts) < 7:
                continue
            gpus.append(
                GpuInfo(
                    index=int(parts[0]),
                    name=parts[1],
                    memory_total_mb=int(float(parts[2])),
                    memory_used_mb=int(float(parts[3])),
                    memory_free_mb=int(float(parts[4])),
                    util_percent=int(float(parts[5])),
                    temperature_c=int(float(parts[6])) if parts[6] else 0,
                )
            )
        return gpus

    @staticmethod
    async def list_processes(node_id: int) -> list[GpuProcess]:
        node = _require_node(await GpuNodeDao.find_by_id(node_id))
        result = await NodeService._run_remote(node, commands.GPU_APPS_CMD)
        processes = []
        for line in result.stdout.splitlines():
            parts = [part.strip() for part in line.split(",")]
            if len(parts) < 3:
                continue
            processes.append(
                GpuProcess(
                    pid=int(parts[0]),
                    process_name=parts[1],
                    used_memory_mb=int(float(parts[2])),
                )
            )
        return processes

    @staticmethod
    async def list_containers(node_id: int) -> list[ContainerInfo]:
        node = _require_node(await GpuNodeDao.find_by_id(node_id))
        result = await NodeService._run_remote(node, commands.DOCKER_PS_CMD)
        containers = []
        for line in result.stdout.splitlines():
            parts = line.split("\t")
            if len(parts) < 5:
                continue
            containers.append(
                ContainerInfo(
                    container_id=parts[0].strip(),
                    name=parts[1].strip(),
                    image=parts[2].strip(),
                    status=parts[3].strip(),
                    ports=parts[4].strip(),
                )
            )
        return containers

    @staticmethod
    def _resolve_subpath(node: GpuNode, subpath: str) -> str:
        resolved = os.path.normpath(os.path.join(node.model_dir, subpath.lstrip("/")))
        if os.path.commonpath([resolved, node.model_dir]) != node.model_dir:
            raise InvalidBrowsePathError()
        return resolved

    @staticmethod
    async def list_model_files(node_id: int, subpath: str = "") -> list[ModelFileInfo]:
        node = _require_node(await GpuNodeDao.find_by_id(node_id))
        resolved = NodeService._resolve_subpath(node, subpath)
        result = await NodeService._run_remote(node, commands.LS_DIR_TMPL.format(path=resolved))
        files = []
        for line in result.stdout.splitlines()[1:]:  # skip "total N" line
            parts = line.split(None, 8)
            if len(parts) < 9 or parts[8] in (".", ".."):
                continue
            name = parts[8]
            try:
                size = int(parts[4])
            except ValueError:
                size = 0
            is_dir = parts[0].startswith("d")
            if is_dir:
                try:
                    du = await NodeService._run_remote(
                        node, commands.DU_DIR_TMPL.format(path=os.path.join(resolved, name))
                    )
                    first = du.stdout.splitlines()[0] if du.stdout.splitlines() else ""
                    size = (
                        int(first.split("\t")[0].strip()) if first and first.split("\t")[0].strip().isdigit() else size
                    )
                except Exception:
                    pass
            files.append(
                ModelFileInfo(
                    name=name,
                    is_dir=is_dir,
                    size_bytes=size,
                    mode=parts[0],
                    mtime=" ".join(parts[5:8]),
                    path=os.path.join(resolved, name),
                )
            )
        return files

    @staticmethod
    async def delete_model_file(node_id: int, path: str) -> None:
        node = _require_node(await GpuNodeDao.find_by_id(node_id))
        resolved = os.path.normpath(path)
        if os.path.commonpath([resolved, node.model_dir]) != node.model_dir:
            raise InvalidBrowsePathError()
        if resolved == node.model_dir or resolved == "/":
            raise InvalidBrowsePathError()
        try:
            result = await run_ssh(_session_for(node), commands.RM_PATH_TMPL.format(path=resolved))
        except Exception as exc:
            raise DeleteModelFileError(msg=str(exc)) from exc
        if result.exit_code != 0:
            raise DeleteModelFileError(msg=result.stderr or f"exit {result.exit_code}")
