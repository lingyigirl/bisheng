"""F078 路由骨架。

- ``management_router`` 挂 ``/api/v1``（平台纳管控制台）
- ``rpc_router`` 挂 ``/api/v2``（外部统一调用，继承 F076 密钥鉴权依赖）

具体端点随各 Wave 的 task 加入；本文件只负责聚合，不承载业务逻辑（C1）。
"""

from fastapi import APIRouter

from bisheng.managed_agent.api.endpoints.agents import router as agents_router
from bisheng.managed_agent.api.endpoints.invoke import router as invoke_router

management_router = APIRouter(tags=["ManagedAgent"])
management_router.include_router(agents_router)
rpc_router = APIRouter(tags=["ManagedAgent"])
rpc_router.include_router(invoke_router)
