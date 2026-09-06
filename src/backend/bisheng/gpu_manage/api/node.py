from fastapi import APIRouter, Depends

from bisheng.common.dependencies.user_deps import UserPayload
from bisheng.common.schemas.api import resp_200
from bisheng.gpu_manage.domain.models.gpu_node import GpuNodeCreate, GpuNodeUpdate
from bisheng.gpu_manage.domain.services.node_service import NodeService

router = APIRouter(prefix="/gpu_node", tags=["GpuNode"], dependencies=[Depends(UserPayload.get_tenant_admin_user)])


@router.post("")
async def create_node(*, node: GpuNodeCreate):
    return resp_200(await NodeService.create_node(node))


@router.get("")
async def list_nodes():
    return resp_200(await NodeService.list_nodes())


@router.get("/{node_id}")
async def get_node(*, node_id: int):
    return resp_200(await NodeService.get_node(node_id))


@router.put("/{node_id}")
async def update_node(*, node_id: int, node: GpuNodeUpdate):
    return resp_200(await NodeService.update_node(node_id, node))


@router.delete("/{node_id}")
async def delete_node(*, node_id: int):
    await NodeService.delete_node(node_id)
    return resp_200(None)


@router.post("/{node_id}/test")
async def test_node(*, node_id: int):
    return resp_200(await NodeService.test_connection(node_id))
