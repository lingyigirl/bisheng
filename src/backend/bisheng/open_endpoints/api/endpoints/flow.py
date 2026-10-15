from uuid import UUID

from fastapi import APIRouter, HTTPException, Request
from loguru import logger

from bisheng.api.services.flow import FlowService
from bisheng.common.services.config_service import settings
from bisheng.open_api.domain.context import get_open_api_principal
from bisheng.open_api.domain.scopes import open_api_scope
from bisheng.open_endpoints.api.endpoints.assistant import get_default_operator

router = APIRouter(prefix="/flows", tags=["OpenAPI", "FlowV2"])


@open_api_scope("workflow:read", guest=True)
@router.get("/{flow_id}", status_code=200)
async def get_flow(request: Request, flow_id: UUID):
    """
    Exposed interfaces for obtaining skill information
    """
    flow_id = flow_id.hex
    logger.info(f"public_get_flow  ip: {request.client.host} flow_id:{flow_id}")
    # Determine if the configuration under is turned on
    # F076(T-14):免登录开关只拦匿名访问;带密钥的服务账号调用不受它影响。
    if get_open_api_principal() is None and not settings.get_from_db("default_operator").get("enable_guest_access"):
        raise HTTPException(status_code=403, detail="No permission to access")
    default_user = get_default_operator()

    return await FlowService.get_one_flow(default_user, flow_id)
