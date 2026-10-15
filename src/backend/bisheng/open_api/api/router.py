"""F076 开放 API 模块自有路由(v1 供管理面、v2 rpc 面只有 whoami)。

管理面 API(服务账号 / 密钥 / 设置)随 T-20~T-22 落 `management_router`;
本文件当前只导出 `rpc_router`(whoami),由 `bisheng/api/router.py` 挂到
`/api/v2` 的 `router_rpc` 上——鉴权依赖在那一层统一生效。
"""

from fastapi import APIRouter

from bisheng.open_api.api.endpoints.auth import router as auth_router

rpc_router = APIRouter()
rpc_router.include_router(auth_router)

__all__ = ["rpc_router"]
