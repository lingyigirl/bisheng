import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from loguru import logger

from bisheng.api.router import router, router_rpc
from bisheng.common.errcode import BaseErrorCode
from bisheng.common.errcode.managed_agent import ManagedAgentErrorBase
from bisheng.common.errcode.open_api import OpenApiErrorBase, OpenApiNotFoundError
from bisheng.common.errcode.scheduled_task import ScheduledTaskErrorBase
from bisheng.common.exceptions.auth import AuthJWTException
from bisheng.common.init_data import init_default_data
from bisheng.common.middleware.admin_scope import AdminScopeMiddleware
from bisheng.common.services.config_service import settings
from bisheng.core.context import close_app_context, initialize_app_context
from bisheng.core.logger import set_logger_config
from bisheng.utils.http_middleware import CustomMiddleware, WebSocketLoggingMiddleware
from bisheng.utils.threadpool import thread_pool

#: F076 T-29(AC-23):密钥鉴权的 /api/v2 调用里,"目标资源不存在或不可见"的各种 legacy
#: 码统一成 11809/404 —— 两种情形不可区分(防枚举)。仅对打了 `open_api_call` 标记的
#: 请求生效;控制台与旧行为不受影响。
_OPEN_API_MASKED_CODES = frozenset({403, 404, 10400, 10962, 10970, 10971, 18000, 18010, 18040})


def handle_http_exception(req: Request, exc: Exception) -> JSONResponse:
    if req.scope.get("open_api_call"):
        code = (
            exc.status_code
            if isinstance(exc, HTTPException)
            else (exc.code if isinstance(exc, BaseErrorCode) else None)
        )
        if code in _OPEN_API_MASKED_CODES:
            logger.info("open_api: masking resource rejection {} -> 11809 for {}", code, req.url.path)
            masked = OpenApiNotFoundError()
            return JSONResponse(status_code=masked.HttpStatus, content=masked.return_resp_instance().model_dump())
    if isinstance(exc, HTTPException):
        msg = {
            "status_code": exc.status_code,
            "status_message": exc.detail["error"] if isinstance(exc.detail, dict) else exc.detail,
        }
    elif isinstance(exc, BaseErrorCode):
        data = {"exception": str(exc), **exc.kwargs} if exc.kwargs else {"exception": str(exc)}
        msg = {"status_code": exc.code, "status_message": exc.message, "data": data}
    else:
        logger.exception("Unhandled exception")
        msg = {"status_code": 500, "status_message": str(exc)}
    logger.error(f"{req.method} {req.url} {exc!s}")
    return JSONResponse(content=msg)


def handle_request_validation_error(req: Request, exc: RequestValidationError) -> JSONResponse:
    msg = {"status_code": status.HTTP_422_UNPROCESSABLE_ENTITY, "status_message": exc.errors()}
    logger.error(f"{req.method} {req.url} {str(exc.errors())[:100]}")
    return JSONResponse(content=msg)


async def handle_open_api_error(req: Request, exc: "OpenApiErrorBase") -> JSONResponse:
    """F076 T-13:开放 API 错误回**真实 HTTP 状态** + 统一信封(design §3 决策 3)。

    与 handle_http_exception 的三点区别:
    1. 状态码取 `exc.HttpStatus`(401/403/404/400/500/503),而不是业务码本体;
       Starlette 按 MRO 匹配,17 个子类全部自动生效。
    2. **不访问 `req.method`** —— WebSocket scope 下没有该属性,访问即
       AttributeError → 握手 500 + traceback(design §5 坑 3)。
    3. 不读请求体、不额外查询,纯映射。

    作用域是全应用(不仅 /api/v2) —— 管理面的 118xx(如 400/11810)同样拿到真状态,
    与上游管理接口的对外口径一致。
    """
    payload = exc.return_resp_instance()
    return JSONResponse(status_code=exc.HttpStatus, content=payload.model_dump())


async def handle_scheduled_task_error(req: Request, exc: "ScheduledTaskErrorBase") -> JSONResponse:
    """F077:定时任务错误**分面**返回(design §3 决策 15)。

    - 密钥鉴权的 ``/api/v2`` 调用(scope 打有 ``open_api_call`` 标记,由 F076 依赖设置)
      → 真 HTTP 状态(``exc.HttpStatus``:400/409/429),对外契约与 F076 同形态;
    - ``/api/v1`` 管理面 → 保持平台既有信封(HTTP 200 + 业务码),**不改变**平台
      前端拦截器对 403/404 的既有行为(console 的 GET 404 会整页跳转,此处不得触发)。

    与 ``handle_open_api_error`` 同样的纪律:**不访问 ``req.method``**(WS scope 下
    没有该属性,访问即 AttributeError → 500 + traceback)。
    """
    payload = exc.return_resp_instance()
    if req.scope.get("open_api_call"):
        return JSONResponse(status_code=exc.HttpStatus, content=payload.model_dump())
    return JSONResponse(content=payload.model_dump())


async def handle_managed_agent_error(req: Request, exc: "ManagedAgentErrorBase") -> JSONResponse:
    """F078:纳管智能体错误**分面**返回(与 F077 同口径)。

    - 密钥鉴权的 ``/api/v2`` 调用 → 真 HTTP 状态(400/403/404/409/429/502);
    - ``/api/v1`` 管理面 → 保持平台既有信封(HTTP 200 + 业务码)。
    不访问 ``req.method``(WS scope 下没有该属性)。
    """
    payload = exc.return_resp_instance()
    headers = None
    if exc.HttpStatus == 429:
        headers = {"Retry-After": str(exc.kwargs.get("retry_after", 60))}
    if req.scope.get("open_api_call"):
        return JSONResponse(status_code=exc.HttpStatus, content=payload.model_dump(), headers=headers)
    return JSONResponse(content=payload.model_dump(), headers=headers)


_EXCEPTION_HANDLERS = {
    HTTPException: handle_http_exception,
    RequestValidationError: handle_request_validation_error,
    OpenApiErrorBase: handle_open_api_error,
    ScheduledTaskErrorBase: handle_scheduled_task_error,
    ManagedAgentErrorBase: handle_managed_agent_error,
    BaseErrorCode: handle_http_exception,
    Exception: handle_http_exception,
}


@asynccontextmanager
async def lifespan(app: FastAPI):
    await initialize_app_context(config=settings)
    await init_default_data()

    # F077 部署便利：默认租户尚无策略行时，按配置播种"要求密钥"。
    # 已有行不覆盖（DB 是事实源）；失败只记日志，不阻塞启动（策略保持缺行=未启用）。
    try:
        from bisheng.open_api.domain.services.tenant_setting_service import seed_default_tenant_policy

        await seed_default_tenant_policy(settings.open_api.default_tenant_enforce_keys)
    except Exception:
        logger.exception("open_api: default-tenant policy seed failed; continuing with DB state")
    # F034: align frozen system permission tiers with newly-added
    # move_file/move_folder on every boot. Idempotent + non-critical — a failure
    # only leaves the admin permission UI stale, so it must never block startup.
    try:
        from bisheng.permission.domain.relation_model_backfill import (
            backfill_relation_model_move_permissions,
        )

        await backfill_relation_model_move_permissions()
    except Exception:
        logger.exception("relation-model move-permission backfill failed; continuing startup")
    # F035: align legacy linsight data with the deepagents task-mode shape on every
    # boot. Both are idempotent + non-critical — failure only leaves stale menu
    # grants / model config (operators can re-run the standalone scripts), so it
    # must never block startup. The SOP->Skill data migration is intentionally NOT
    # here (it writes object storage and is heavier — stays a manual ops script).
    try:
        from bisheng.permission.domain.linsight_task_mode_menu_backfill import (
            backfill_linsight_task_mode_web_menu,
        )

        await backfill_linsight_task_mode_web_menu()
    except Exception:
        logger.exception("linsight task-mode menu backfill failed; continuing startup")
    try:
        from bisheng.llm.domain.services.linsight_default_model_backfill import (
            backfill_linsight_default_model,
        )

        await backfill_linsight_default_model()
    except Exception:
        logger.exception("linsight default-model backfill failed; continuing startup")
    # LangfuseInstance.update()
    yield
    thread_pool.tear_down()
    await close_app_context()


def _mount_local_storage(app: FastAPI) -> None:
    """Local-storage mode: answer /<bucket>/<object> straight off disk.

    MinIO deployments route these paths through nginx / vite to MinIO; when the
    local backend is active the objects live on this host, so the app serves
    them itself. No-op for any other storage type.
    """
    object_storage = settings.object_storage
    if (object_storage.type or "").strip().lower() != "local" or not object_storage.local:
        return

    from pathlib import Path

    from starlette.staticfiles import StaticFiles

    local = object_storage.local
    root = Path(local.root)
    for bucket in (local.public_bucket, local.tmp_bucket):
        app.mount(
            f"/{bucket}",
            StaticFiles(directory=str(root / bucket), check_dir=False),
            name=f"local-storage-{bucket}",
        )


def create_app():
    """Create the FastAPI app and include the router."""

    app = FastAPI(
        exception_handlers=_EXCEPTION_HANDLERS,
        lifespan=lifespan,
    )

    # 前端 axios 使用 withCredentials=true 时，浏览器禁止 ACAO 为 *。
    # 可通过环境变量 BISHENG_CORS_ORIGINS 覆盖，逗号分隔，例如：
    # BISHENG_CORS_ORIGINS=http://localhost:3001,http://127.0.0.1:3001
    _cors_raw = (os.getenv("BISHENG_CORS_ORIGINS") or "").strip()
    if _cors_raw:
        origins = [o.strip() for o in _cors_raw.split(",") if o.strip()]
    else:
        origins = [
            "http://localhost:3000",
            "http://localhost:3001",
            "http://127.0.0.1:3000",
            "http://127.0.0.1:3001",
            "http://localhost:5173",
            "http://127.0.0.1:5173",
        ]

    @app.get("/health")
    def get_health():
        return {"status": "OK"}

    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Starlette middleware uses LIFO registration — the middleware added
    # latest is the *outermost* on inbound. We want CustomMiddleware (JWT
    # decode + visible_tenant_ids) to run *before* AdminScopeMiddleware on
    # the inbound path, which means AdminScopeMiddleware must be added
    # *first* (inner). See ``common/middleware/admin_scope.py`` docstring.
    app.add_middleware(AdminScopeMiddleware)
    app.add_middleware(CustomMiddleware)
    app.add_middleware(WebSocketLoggingMiddleware)

    @app.exception_handler(AuthJWTException)
    def authjwt_exception_handler(request: Request, exc: AuthJWTException):
        return JSONResponse(status_code=401, content={"detail": str(exc)})

    app.include_router(router)
    app.include_router(router_rpc)
    from bisheng.department.api.endpoints.department_limit import (
        router as department_limit_router,
    )

    app.include_router(
        department_limit_router,
        prefix="/api/department-limit",
        tags=["Department traffic"],
    )
    _mount_local_storage(app)

    if settings.debug:
        import tracemalloc

        tracemalloc.start()

    return app


app = create_app()

if __name__ == "__main__":
    import uvicorn

    set_logger_config(settings.logger_conf)

    uvicorn.run(app, host="0.0.0.0", port=7860, workers=1, log_config=None)
