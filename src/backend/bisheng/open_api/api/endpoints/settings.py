"""F076 T-22:开放 API 租户设置端点(spec AC-34,design §4.2)。

`GET/PUT /api/v1/open-api/settings` —— 本租户的强制开关。

**为什么响应里带 `deployment_enforced` 与 `effective`**:强制 = 部署级 ∧ 租户级,
管理员只看自己这一行会困惑"我明明开了为什么没生效"——把总闸状态一并回显,
前端才能把原因说清楚(部署级由配置文件控制,租户管理员改不了)。
"""

from fastapi import APIRouter, Depends, Request
from loguru import logger
from pydantic import BaseModel

from bisheng.common.dependencies.user_deps import UserPayload
from bisheng.common.schemas.api import resp_200
from bisheng.common.services.config_service import settings as app_settings
from bisheng.core.context.tenant import get_current_tenant_id
from bisheng.database.models.tenant import ROOT_TENANT_ID
from bisheng.open_api.domain.constants import OpenApiAuditAction
from bisheng.open_api.domain.services import base_url_service, call_audit_service, tenant_setting_service
from bisheng.utils import get_request_ip

router = APIRouter(prefix="/open-api", tags=["OpenApiManagement"])


class TenantSettingReq(BaseModel):
    enforce_keys: bool


def _acting_tenant(login_user: UserPayload) -> int:
    return get_current_tenant_id() or login_user.tenant_id


def _serialize(tenant_id: int, row) -> dict:
    deployment = bool(app_settings.open_api.enforce_keys)
    tenant_flag = bool(row.enforce_keys) if row is not None else False
    return {
        "tenant_id": tenant_id,
        "enforce_keys": tenant_flag,
        "deployment_enforced": deployment,
        "effective": deployment and tenant_flag,
        "update_time": row.update_time.isoformat() if row is not None and row.update_time else None,
    }


@router.get("/settings")
async def get_settings(login_user: UserPayload = Depends(UserPayload.get_tenant_admin_user)):
    """读取本租户策略;`缺行 = 未启用`(fail-closed)。"""
    tenant_id = _acting_tenant(login_user)
    row = await tenant_setting_service.get_policy(tenant_id)
    return resp_200(data=_serialize(tenant_id, row))


@router.put("/settings")
async def update_settings(
    req: TenantSettingReq,
    login_user: UserPayload = Depends(UserPayload.get_tenant_admin_user),
    request: Request = None,  # FastAPI 按注解注入;默认值让测试可直接调用
):
    """写入本租户开关——**立即生效**(写后清缓存,spec AC-34)。

    审计动作 `open_api.settings.update`(T-27);同时记 `deployment_enforced`——
    否则"我开了为什么没生效"在审计里无从解释(强制 = 部署级 ∧ 租户级)。
    """
    tenant_id = _acting_tenant(login_user)
    # 缺行 = 未启用(fail-closed):before 记 None,读的是策略行本身而不是生效态。
    before_row = await tenant_setting_service.get_policy(tenant_id)
    row = await tenant_setting_service.upsert_policy(tenant_id, enforce_keys=req.enforce_keys)
    try:
        call_audit_service.record_admin_action(
            login_user=login_user,
            tenant_id=tenant_id,
            operator_tenant_id=get_current_tenant_id() or ROOT_TENANT_ID,
            action=OpenApiAuditAction.SETTINGS_UPDATE.value,
            target_type="open_api_tenant_setting",
            target_id=tenant_id,
            before={"enforce_keys": before_row.enforce_keys if before_row is not None else None},
            after={"enforce_keys": row.enforce_keys},
            extra={"deployment_enforced": bool(app_settings.open_api.enforce_keys)},
            ip_address=get_request_ip(request) if request is not None else None,
        )
    except Exception:  # 审计绝不能改变管理动作的结果
        logger.opt(exception=True).warning("open_api: settings audit enqueue failed")
    return resp_200(data=_serialize(tenant_id, row))


@router.get("/base-url")
async def get_base_url(
    login_user: UserPayload = Depends(UserPayload.get_tenant_admin_user),
    request: Request = None,  # FastAPI 按注解注入;默认值让测试可直接调用
):
    """接入指南的地址来源:运维配置 + 本机探测值。

    探测值只在没有配置时给前端兜底——反向代理/HTTPS 部署下进程看不到对外地址,
    猜测值可能被防火墙挡住,所以它**永远不覆盖** `open_api.public_base_url`。
    两个值都回带,管理员与排障人员才能看清默认地址到底从哪来。
    """
    sources = base_url_service.resolve_base_url_sources(app_settings.open_api.public_base_url, request)
    return resp_200(data=sources)
