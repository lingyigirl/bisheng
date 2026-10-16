from fastapi.exceptions import HTTPException
from sqlmodel import select

from bisheng.common.dependencies.user_deps import UserPayload
from bisheng.common.errcode.http_error import NotFoundError
from bisheng.common.errcode.open_api import OpenApiNotFoundError
from bisheng.common.services.config_service import settings
from bisheng.core.context.tenant import DEFAULT_TENANT_ID, bypass_tenant_filter, set_current_tenant_id
from bisheng.core.database import get_sync_db_session
from bisheng.database.models.tenant import UserTenant, UserTenantDao
from bisheng.open_api.domain.context import get_open_api_principal
from bisheng.user.domain.models.user import UserDao


def _get_active_tenant_id_sync(user_id: int) -> int:
    with bypass_tenant_filter():
        with get_sync_db_session() as session:
            row = session.exec(
                select(UserTenant).where(
                    UserTenant.user_id == user_id,
                    UserTenant.is_active == 1,
                )
            ).first()
            return row.tenant_id if row else DEFAULT_TENANT_ID


def get_default_operator() -> UserPayload:
    """Resolve the default operator and seed the per-request tenant ContextVar.

    The HTTP middleware only sets ``current_tenant_id`` from JWT cookies, so
    OpenAPI calls (which authenticate via the configured default_operator
    instead of a JWT) leave the ContextVar unset. Any DB query touching a
    tenant-aware table then raises ``NoTenantContextError`` under
    ``multi_tenant.enabled=true``. Seed the ContextVar here so every
    ``/api/v2/*`` endpoint that depends on this helper is multi-tenant safe.

    F076: when an open-API principal is installed (key authentication, T-06),
    the acting identity is the service account's resource owner and the tenant
    ContextVar has already been rebuilt by the dependency -- return it as-is.
    """
    principal = get_open_api_principal()
    if principal is not None:
        return principal.owner_payload
    user_id = settings.get_from_db("default_operator").get("user")
    if not user_id:
        raise HTTPException(status_code=500, detail="Not configureddefault_operatorIIuserConfigure")
    # Find default user information
    login_user = UserDao.get_user(user_id)
    if not login_user:
        raise HTTPException(status_code=500, detail="not founddefault_operatorIIuserUser Information")
    tenant_id = _get_active_tenant_id_sync(login_user.user_id)
    login_user = UserPayload.init_login_user_sync(
        user_id=login_user.user_id,
        user_name=login_user.user_name,
        tenant_id=tenant_id,
    )
    set_current_tenant_id(tenant_id)
    return login_user


async def get_default_operator_async() -> UserPayload:
    """Async counterpart of :func:`get_default_operator` — see its docstring.

    Seeding the tenant ContextVar here is what makes default-operator-backed
    OpenAPI endpoints work under ``multi_tenant.enabled=true``.

    F076: an installed open-API principal wins -- see the sync twin.
    """
    principal = get_open_api_principal()
    if principal is not None:
        return principal.owner_payload
    user_id = (await settings.aget_from_db("default_operator")).get("user")
    if not user_id:
        raise HTTPException(status_code=500, detail="Not configureddefault_operatorIIuserConfigure")
    # Find default user information
    login_user = await UserDao.aget_user(user_id)
    if not login_user:
        raise HTTPException(status_code=500, detail="not founddefault_operatorIIuserUser Information")
    active = await UserTenantDao.aget_active_user_tenant(login_user.user_id)
    tenant_id = active.tenant_id if active else DEFAULT_TENANT_ID
    login_user = await UserPayload.init_login_user(
        user_id=login_user.user_id,
        user_name=login_user.user_name,
        tenant_id=tenant_id,
    )
    set_current_tenant_id(tenant_id)
    return login_user


async def resolve_operator(user_id: int | None = None) -> UserPayload:
    """Resolve the acting identity for v2 filelib endpoints (F030 AD-02).

    - ``user_id`` omitted → fall back to the configured default operator
      (existing behaviour, unchanged).
    - ``user_id`` provided → build a ``UserPayload`` for that target user so
      列表 / 检索 / 文件列表 are filtered by *that user's* visibility scope and
      ``permission_ids`` are computed under the same identity. This is the
      "代用户" protocol F029 deferred for ``/api/v2/filelib/retrieve``.

    Seeds ``current_tenant_id`` with the resolved user's active tenant so
    tenant-aware queries stay multi-tenant safe (mirrors get_default_operator_async).

    F076: an installed open-API principal wins outright -- even when a caller
    passes ``user_id``, the identity cannot be caller-specified (INV-23 ③; the
    dependency already rejected bare ``user_id`` with 11817 before we get here).
    """
    principal = get_open_api_principal()
    if principal is not None:
        return principal.owner_payload
    if user_id is None:
        return await get_default_operator_async()

    target_user = await UserDao.aget_user(user_id)
    if not target_user:
        raise NotFoundError.http_exception()
    active = await UserTenantDao.aget_active_user_tenant(target_user.user_id)
    tenant_id = active.tenant_id if active else DEFAULT_TENANT_ID
    login_user = await UserPayload.init_login_user(
        user_id=target_user.user_id,
        user_name=target_user.user_name,
        tenant_id=tenant_id,
    )
    set_current_tenant_id(tenant_id)
    return login_user

# ---------------------------------------------------------------------------
# F076 T-29(AC-23):密钥调用时的资源可见性收口
# ---------------------------------------------------------------------------


def _is_key_authenticated_call() -> bool:
    """本次请求是否由**密钥鉴权**(F076 T-06 装上 principal)。

    总闸关 / 无名旧请求下为 False —— 这些路径必须保持升级前的行为逐字不变
    (spec AC-21),所以所有新增可见性校验都以它为闸。
    """
    return get_open_api_principal() is not None


async def assert_open_api_app_visible(flow_id: str | int, login_user: UserPayload, *, permission_ids=("use_app",)) -> None:
    """密钥调用时:目标应用(工作流/助手)不在该身份可见范围 → 404/11809(防枚举)。

    不可见与不存在**同码同状态**,调用方无法据此探测资源是否存在(AC-23)。
    非密钥调用直接放行(旧行为)。
    """
    if not _is_key_authenticated_call():
        return
    from bisheng.permission.domain.services.application_permission_service import ApplicationPermissionService

    if not await ApplicationPermissionService.has_any_permission_async(
        login_user, "workflow", str(flow_id), list(permission_ids)
    ):
        raise OpenApiNotFoundError()


async def assert_open_api_knowledge_visible(knowledge_id: str | int, login_user: UserPayload, *, write: bool = False) -> None:
    """密钥调用时:目标知识库/空间的读(或写)权限不在该身份范围 → 404/11809。

    复用知识模块自己的权限判定(`ensure_knowledge_read/write_async`),把它的
    拒绝类统一成 11809;非密钥调用直接放行(旧行为)。
    """
    if not _is_key_authenticated_call():
        return
    from bisheng.knowledge.domain.services.knowledge_permission_service import KnowledgePermissionService

    try:
        if write:
            await KnowledgePermissionService.ensure_knowledge_write_async(login_user, str(knowledge_id))
        else:
            await KnowledgePermissionService.ensure_knowledge_read_async(login_user, str(knowledge_id))
    except OpenApiNotFoundError:
        raise
    except Exception as exc:
        # 无权 / 不存在 / 空间类错误 —— 统一 11809;其余异常照常抛(不吞)。
        if getattr(exc, "code", None) in (403, 404, 10970, 10971, 18000, 18010, 18040):
            raise OpenApiNotFoundError() from exc
        raise

def assert_open_api_app_visible_sync(flow_id: str | int, login_user: UserPayload, *, permission_ids=("use_app",)) -> None:
    """`assert_open_api_app_visible` 的同步版(同步端点用,如 /api/v2/chat/*)。"""
    if not _is_key_authenticated_call():
        return
    from bisheng.permission.domain.services.application_permission_service import ApplicationPermissionService

    if not ApplicationPermissionService.has_any_permission_sync(
        login_user, "workflow", str(flow_id), list(permission_ids)
    ):
        raise OpenApiNotFoundError()

def assert_open_api_knowledge_visible_sync(
    knowledge_id: str | int, login_user: UserPayload, *, write: bool = False
) -> None:
    """`assert_open_api_knowledge_visible` 的同步版(同步端点用,如 QA 族)。"""
    if not _is_key_authenticated_call():
        return
    from bisheng.knowledge.domain.services.knowledge_permission_service import KnowledgePermissionService

    try:
        if write:
            KnowledgePermissionService.ensure_knowledge_write_sync(login_user, str(knowledge_id))
        else:
            KnowledgePermissionService.ensure_knowledge_read_sync(login_user, str(knowledge_id))
    except OpenApiNotFoundError:
        raise
    except Exception as exc:
        if getattr(exc, "code", None) in (403, 404, 10970, 10971, 18000, 18010, 18040):
            raise OpenApiNotFoundError() from exc
        raise
