"""F077 定时任务模块自有路由（双导出，先例 ``open_api/api/router.py``）。

- ``management_router``：管理面（任务 CRUD / 立即运行 / 可用运行身份 / cron 预览 / NL 草稿），
  挂到 ``/api/v1``；各端点**自行**声明应用级 ``edit_app`` 校验（design 决策 11——每个入口独立
  校验，不做统一路由级依赖，拒绝文案需按动作可读）。
- ``rpc_router``：事件触发面（``POST /api/v2/schedule/trigger``），挂到继承 F076 密钥鉴权
  依赖的 ``router_rpc`` 之下；端点的 scope 在 ``open_api/domain/scopes.py`` 逐条登记，
  漏标即 500/11811（design 决策 12）。

端点文件随任务落地：``endpoints/tasks.py`` / ``nl.py``（T-15、T-20）、``endpoints/trigger.py``（T-18）。
"""

from fastapi import APIRouter

from bisheng.scheduled_task.api.endpoints.tasks import router as tasks_router

management_router = APIRouter()
management_router.include_router(tasks_router)
rpc_router = APIRouter()

__all__ = ["management_router", "rpc_router"]
