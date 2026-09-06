from fastapi import APIRouter, Depends, Query

from bisheng.common.dependencies.user_deps import UserPayload
from bisheng.common.schemas.api import resp_200
from bisheng.gpu_manage.domain.models.deploy_instance import DeployInstanceCreate
from bisheng.gpu_manage.domain.models.deploy_template import DeployTemplateCreate, DeployTemplateUpdate
from bisheng.gpu_manage.domain.services.deploy_service import DeployService

router = APIRouter(prefix="/gpu_deploy", tags=["GpuDeploy"], dependencies=[Depends(UserPayload.get_tenant_admin_user)])


@router.get("/template")
async def list_templates():
    return resp_200(await DeployService.list_templates())


@router.post("/template")
async def create_template(*, template: DeployTemplateCreate):
    return resp_200(await DeployService.create_template(template))


@router.put("/template/{template_id}")
async def update_template(*, template_id: int, template: DeployTemplateUpdate):
    return resp_200(await DeployService.update_template(template_id, template))


@router.delete("/template/{template_id}")
async def delete_template(*, template_id: int):
    await DeployService.delete_template(template_id)
    return resp_200(None)


@router.post("/instance")
async def create_instance(*, instance: DeployInstanceCreate):
    return resp_200(await DeployService.create_instance(instance))


@router.get("/instance")
async def list_instances(*, node_id: int = Query(default=None)):
    return resp_200(await DeployService.list_instances(node_id))


@router.get("/instance/{instance_id}/status")
async def get_instance_status(*, instance_id: int):
    return resp_200(await DeployService.get_status(instance_id))


@router.get("/instance/{instance_id}/logs")
async def get_instance_logs(*, instance_id: int, tail: int = Query(default=None)):
    return resp_200({"logs": await DeployService.get_logs(instance_id, tail)})


@router.post("/instance/{instance_id}/start")
async def start_instance(*, instance_id: int):
    return resp_200({"status": await DeployService.start_instance(instance_id)})


@router.post("/instance/{instance_id}/stop")
async def stop_instance(*, instance_id: int):
    return resp_200({"status": await DeployService.stop_instance(instance_id)})


@router.post("/instance/{instance_id}/restart")
async def restart_instance(*, instance_id: int):
    return resp_200({"status": await DeployService.restart_instance(instance_id)})


@router.delete("/instance/{instance_id}")
async def delete_instance(*, instance_id: int):
    await DeployService.delete_instance(instance_id)
    return resp_200(None)
