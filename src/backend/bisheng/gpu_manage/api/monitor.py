from fastapi import APIRouter, Depends, Query

from bisheng.common.dependencies.user_deps import UserPayload
from bisheng.common.schemas.api import resp_200
from bisheng.gpu_manage.domain.services.node_service import NodeService
from bisheng.gpu_manage.domain.services.worker_service import list_workers

router = APIRouter(
    prefix="/gpu_monitor", tags=["GpuMonitor"], dependencies=[Depends(UserPayload.get_tenant_admin_user)]
)


@router.get("/workers")
async def get_workers():
    return resp_200(await list_workers())


@router.get("/{node_id}/gpus")
async def get_gpus(*, node_id: int):
    return resp_200(await NodeService.list_gpus(node_id))


@router.get("/{node_id}/processes")
async def get_processes(*, node_id: int):
    return resp_200(await NodeService.list_processes(node_id))


@router.get("/{node_id}/containers")
async def get_containers(*, node_id: int):
    return resp_200(await NodeService.list_containers(node_id))


@router.get("/{node_id}/files")
async def get_files(*, node_id: int, subpath: str = Query(default="")):
    return resp_200(await NodeService.list_model_files(node_id, subpath))


@router.delete("/{node_id}/files")
async def delete_file(*, node_id: int, path: str = Query(...)):
    await NodeService.delete_model_file(node_id, path)
    return resp_200(None)
