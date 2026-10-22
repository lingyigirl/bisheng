from uuid import UUID

from fastapi import APIRouter, HTTPException, Request
from loguru import logger

from bisheng.api.services.flow import FlowService
from bisheng.common.services.config_service import settings
from bisheng.open_api.domain.agent_scope import assert_open_api_agent_allowed
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
    # F076 增量(2026-10-22):每 Key 智能体白名单(非密钥调用 no-op)。
    assert_open_api_agent_allowed("workflow", flow_id)
    logger.info(f"public_get_flow  ip: {request.client.host} flow_id:{flow_id}")
    # Determine if the configuration under is turned on
    # F076(T-14):免登录开关只拦匿名访问;带密钥的服务账号调用不受它影响。
    if get_open_api_principal() is None and not settings.get_from_db("default_operator").get("enable_guest_access"):
        raise HTTPException(status_code=403, detail="No permission to access")
    default_user = get_default_operator()
    res = await FlowService.get_one_flow(default_user, flow_id)

    # F076 T-19:补"可用工具清单"(全部 agent 节点 tool_list 的并集),供对接方
    # 构造 `tools[]`。原始节点数据本就在 data.nodes 里,这里是给一个稳定、
    # 免解析的字段名;多一次主键读,可忽略。
    from bisheng.database.models.flow import FlowDao
    from bisheng.open_api.domain.services import tool_scope_service

    flow_row = await FlowDao.aget_flow_by_id(flow_id)
    if flow_row is not None:
        payload = res.model_dump()
        data = payload.get("data")
        if isinstance(data, dict):
            data["tools"] = tool_scope_service.workflow_tool_manifest(flow_row.data)
        return payload
    return res
