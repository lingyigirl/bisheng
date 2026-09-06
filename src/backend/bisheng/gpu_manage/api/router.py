from fastapi import APIRouter

from bisheng.gpu_manage.api.deploy import router as deploy_router
from bisheng.gpu_manage.api.monitor import router as monitor_router
from bisheng.gpu_manage.api.node import router as node_router

router = APIRouter()

router.include_router(node_router)
router.include_router(monitor_router)
router.include_router(deploy_router)
