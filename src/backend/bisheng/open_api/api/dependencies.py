"""F076 开放 API 鉴权依赖(T-06,design §4.1 的 ①-⑧)。

挂在 `/api/v2` 路由前缀上,HTTP 与 WebSocket 共用。判定口径见 design **决策 10**:

- 部署总闸关 → **完全旧行为**(忽略 Authorization,连无效密钥也不拦,spec AC-21);
- 总闸开 + **无名请求** → 按默认租户的租户开关:开=11801,关=旧行为(分租户灰度);
- 总闸开 + **带密钥** → 永远校验、永远按服务账号身份执行(不因租户开关回落默认操作员)。

失败语义(design §4.3):HTTP 上抛 118xx(由 T-13 的专用处理器落成真实状态码);
**WebSocket 上一切失败统一转 `WebSocketException(1008)`** —— 本仓的全局异常处理器
会访问 `req.method`,在 WS scope 下不存在,直接抛 HTTPException 会变成握手 500 +
traceback(design §5 坑 3)。
"""

from __future__ import annotations

from collections.abc import AsyncGenerator

from fastapi import WebSocket, WebSocketException
from loguru import logger
from starlette import status as http_status
from starlette.requests import HTTPConnection, Request

from bisheng.common.errcode.base import BaseErrorCode
from bisheng.common.errcode.open_api import (
    OpenApiAccountDisabledError,
    OpenApiEndpointUnregisteredError,
    OpenApiIdentityInputRejectedError,
    OpenApiKeyMissingError,
    OpenApiOwnerPrivilegedError,
    OpenApiScopeMissingError,
)
from bisheng.common.services.config_service import settings
from bisheng.core.context.tenant import (
    DEFAULT_TENANT_ID,
    set_current_tenant_id,
    set_visible_tenant_ids,
)
from bisheng.open_api.domain.context import (
    OpenApiPrincipal,
    reset_open_api_principal,
    set_open_api_principal,
)
from bisheng.open_api.domain.models.dao import ServiceAccountDao
from bisheng.open_api.domain.scopes import get_open_api_scope_marker, is_scope_granted
from bisheng.open_api.domain.services import tenant_setting_service
from bisheng.open_api.domain.services.credential_validator import validate_bearer
from bisheng.open_api.domain.services.identity_service import load_owner_payload, owner_is_privileged

#: removed-in-v2 的裸身份参数;出现即拒(spec AC-25 / INV-23 ③),不静默忽略。
_BANNED_IDENTITY_FIELDS = ("user_id",)


async def _assert_no_identity_input(conn: HTTPConnection) -> None:
    """HTTP 请求里出现裸 `user_id`(query / form)一律 11817。

    body JSON 不在这里解析 —— 鉴权依赖不读请求体(design §5 坑 4):
    端点自身在 schema 层已经不接受这些字段,JSON 里的 `user_id` 会被
    Pydantic 忽略或报 422,grep 层面的收口由 T-12 的守卫测试兜底。
    """
    for field in _BANNED_IDENTITY_FIELDS:
        if field in conn.query_params:
            raise OpenApiIdentityInputRejectedError()
    if isinstance(conn, Request) and conn.method in ("POST", "PUT", "PATCH"):
        content_type = conn.headers.get("content-type", "")
        if content_type.startswith(("multipart/form-data", "application/x-www-form-urlencoded")):
            form = await conn.form()
            for field in _BANNED_IDENTITY_FIELDS:
                if field in form:
                    raise OpenApiIdentityInputRejectedError()


async def _resolve_principal(conn: HTTPConnection) -> OpenApiPrincipal | None:
    """完成 ①-⑦ 的全部校验;返回 None 表示"走旧行为",否则返回可装载的主体。"""
    # ① 部署总闸(关 → 旧行为,AC-21)
    if not settings.open_api.enforce_keys:
        return None

    authorization = conn.headers.get("authorization")

    # ② 无名请求:按默认租户的租户开关判定(决策 10)
    if authorization is None:
        if await tenant_setting_service.is_enforced(DEFAULT_TENANT_ID):
            raise OpenApiKeyMissingError()
        return None

    # ③ 凭据校验(11801 / 11802 / 11803 / 11804)
    credential = await validate_bearer(authorization)

    # ④ 账号活性(11805)
    account = await ServiceAccountDao.aget_by_id(credential.service_account_id)
    if account is None or not account.is_enabled:
        raise OpenApiAccountDisabledError()

    # ⑤ 端点标记与权限位(11811 漏标 / 11807 缺位)
    endpoint = conn.scope.get("endpoint")
    marker = get_open_api_scope_marker(endpoint)
    if marker is None:
        logger.error("open_api: /api/v2 endpoint without scope marker: {}", endpoint)
        raise OpenApiEndpointUnregisteredError()
    if not is_scope_granted(marker.scope, set(credential.scopes or [])):
        raise OpenApiScopeMissingError()

    # ⑥ 身份输入收口(11817;WS 没有 query 以外的输入面,仍做统一检查)
    await _assert_no_identity_input(conn)

    # ⑦ owner 装载 + 运行时防提权(11816 / 11805)
    owner = await load_owner_payload(
        owner_user_id=account.resource_owner_user_id,
        tenant_id=credential.tenant_id,
    )
    if owner is None:
        raise OpenApiAccountDisabledError()
    if await owner_is_privileged(owner, credential.tenant_id):
        logger.warning(
            "open_api: service-account owner {} of credential {} holds admin privileges; refusing",
            account.resource_owner_user_id,
            credential.id,
        )
        raise OpenApiOwnerPrivilegedError()

    return OpenApiPrincipal(
        credential_id=credential.id,
        tenant_id=credential.tenant_id,
        service_account_id=account.id,
        owner_user_id=account.resource_owner_user_id,
        key_mask=credential.key_mask,
        scopes=tuple(credential.scopes or ()),
        owner_payload=owner,
    )


def _install_context(principal: OpenApiPrincipal):
    """⑧ 身份装载:租户上下文重建(决策 9)+ principal 装进 ContextVar。

    中间件已把带密钥请求的租户钉在 ROOT(design §5 坑 1),这里必须显式重设;
    `visible_tenant_ids` 给 `{root, 本租户}`,与 F012 中间件对子租户的口径一致。
    """
    tenant_token = set_current_tenant_id(principal.tenant_id)
    visible_token = set_visible_tenant_ids(frozenset({DEFAULT_TENANT_ID, principal.tenant_id}))
    principal_token = set_open_api_principal(principal)
    return tenant_token, visible_token, principal_token


def _reset_context(tokens) -> None:
    from bisheng.core.context.tenant import current_tenant_id, visible_tenant_ids

    tenant_token, visible_token, principal_token = tokens
    reset_open_api_principal(principal_token)
    visible_tenant_ids.reset(visible_token)
    current_tenant_id.reset(tenant_token)


async def verify_open_api_access(conn: HTTPConnection) -> AsyncGenerator[None, None]:
    """`/api/v2` 的路由级鉴权依赖(HTTP 与 WS 共用;见模块 docstring)。"""
    try:
        principal = await _resolve_principal(conn)
    except BaseErrorCode as exc:
        if isinstance(conn, WebSocket):
            # 预 accept 抛 1008 → 客户端见握手 403(design §4.3);reason 只带业务码,
            # 细节留在服务端日志,避免向未认证的对端泄露判断依据。
            raise WebSocketException(code=http_status.WS_1008_POLICY_VIOLATION, reason=str(exc.code)) from exc
        raise

    if principal is None:  # 旧行为分支:总闸关,或无名请求且租户未开启
        yield
        return

    tokens = _install_context(principal)
    try:
        yield
    finally:
        _reset_context(tokens)
