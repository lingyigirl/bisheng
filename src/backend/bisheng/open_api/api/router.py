"""F076 开放 API 模块自有路由。

- `rpc_router`:v2 面,当前只有 whoami(挂到 `/api/v2`,鉴权依赖在那一层统一生效);
- `management_router`:管理面(服务账号 / 密钥 / 设置),挂到 `/api/v1`,
  各端点自行声明 `get_tenant_admin_user` 依赖(T-20~T-22)。
"""

from fastapi import APIRouter

from bisheng.open_api.api.endpoints.auth import router as auth_router
from bisheng.open_api.api.endpoints.grants import router as grants_router
from bisheng.open_api.api.endpoints.service_account import router as service_account_router
from bisheng.open_api.api.endpoints.service_account_keys import router as service_account_keys_router
from bisheng.open_api.api.endpoints.service_account_keys import scopes_router
from bisheng.open_api.api.endpoints.settings import router as settings_router

rpc_router = APIRouter()
rpc_router.include_router(auth_router)

management_router = APIRouter()
management_router.include_router(service_account_router)
management_router.include_router(service_account_keys_router)
management_router.include_router(scopes_router)
management_router.include_router(grants_router)
management_router.include_router(settings_router)

__all__ = ["management_router", "rpc_router"]
