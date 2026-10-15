"""F076 T-22:开放 API 租户设置端点(spec AC-34,design §4.2)。

`GET/PUT /api/v1/open-api/settings` —— 本租户的强制开关。

**为什么响应里带 `deployment_enforced` 与 `effective`**:强制 = 部署级 ∧ 租户级,
管理员只看自己这一行会困惑"我明明开了为什么没生效"——把总闸状态一并回显,
前端才能把原因说清楚(部署级由配置文件控制,租户管理员改不了)。
"""

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from bisheng.common.dependencies.user_deps import UserPayload
from bisheng.common.schemas.api import resp_200
from bisheng.common.services.config_service import settings as app_settings
from bisheng.core.context.tenant import get_current_tenant_id
from bisheng.open_api.domain.services import tenant_setting_service

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
):
    """写入本租户开关——**立即生效**(写后清缓存,spec AC-34)。

    注:审计动作 `open_api.settings.update` 随 T-27 的审计落地一并接入。
    """
    tenant_id = _acting_tenant(login_user)
    row = await tenant_setting_service.upsert_policy(tenant_id, enforce_keys=req.enforce_keys)
    return resp_200(data=_serialize(tenant_id, row))
