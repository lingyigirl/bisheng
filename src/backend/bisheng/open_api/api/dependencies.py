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

import time
from collections.abc import AsyncGenerator

from fastapi import HTTPException, WebSocket, WebSocketException
from fastapi.exceptions import RequestValidationError
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
    OpenApiTenantDisabledError,
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
from bisheng.open_api.domain.services import call_audit_service, tenant_setting_service
from bisheng.open_api.domain.services.credential_validator import validate_bearer
from bisheng.open_api.domain.services.identity_service import load_owner_payload, owner_is_privileged
from bisheng.utils import get_request_ip

#: removed-in-v2 的裸身份参数;出现即拒(spec AC-25 / INV-23 ③),不静默忽略。
_BANNED_IDENTITY_FIELDS = ("user_id",)


async def _assert_no_identity_input(conn: HTTPConnection) -> None:
    """HTTP 请求里出现裸 `user_id`(query / form / JSON body)一律 11817。

    三个入口都要查(INV-23 ③ 的原文是"任何形式,不得静默忽略"):

    - query / form:直接查参数;
    - **JSON body**:T-28 复核发现 `filelib` retrieve/add_qa/add_relative_qa、`chat`
      sync/messages 的 schema 里仍带着 `user_id`,密钥调用时被静默覆盖成 principal ——
      虽然不会冒充谁,但违反"不得静默忽略",所以在这里显式拒绝。**只解析 JSON**:
      multipart/表单已在上面按字段名拦掉,大文件上传绝不能在这里整包读(design §5 坑 4)。
      Starlette 会缓存 body,端点随后仍能正常解析。
    """
    for field in _BANNED_IDENTITY_FIELDS:
        if field in conn.query_params:
            raise OpenApiIdentityInputRejectedError()
    if not isinstance(conn, Request) or conn.method not in ("POST", "PUT", "PATCH"):
        return

    content_type = conn.headers.get("content-type", "")
    if content_type.startswith(("multipart/form-data", "application/x-www-form-urlencoded")):
        form = await conn.form()
        for field in _BANNED_IDENTITY_FIELDS:
            if field in form:
                raise OpenApiIdentityInputRejectedError()
        return

    if "application/json" not in content_type:
        return
    try:
        payload = await conn.json()
    except Exception:
        # 非法 JSON 交给端点的 schema 校验去报 422,这里不抢戏。
        return
    if isinstance(payload, dict):
        for field in _BANNED_IDENTITY_FIELDS:
            if field in payload:
                raise OpenApiIdentityInputRejectedError()


async def _get_redis():
    """Indirection for tests; production always uses the shared Redis client."""
    from bisheng.core.cache.redis_manager import get_redis_client

    return await get_redis_client()


async def _assert_tenant_active(tenant_id: int | None) -> None:
    """11806: reject credentials whose tenant is disabled/archived.

    The global HTTP middleware checks the *JWT* tenant (ROOT for key-based
    requests, see design §5 pitfall 1), so the open-API dependency must check
    the credential's own tenant. Redis blacklist is the fast path written by
    ``TenantService.aupdate_tenant_status``; **on a cache miss or Redis failure**
    we confirm against the authoritative tenant row (fail-closed) so a flushed
    blacklist cannot silently re-enable a disabled tenant. The root tenant is
    system-protected (cannot be disabled) and is skipped.
    """
    if not tenant_id:
        raise OpenApiTenantDisabledError()

    from bisheng.tenant.domain.services.tenant_service import DISABLED_TENANT_KEY

    try:
        redis = await _get_redis()
        if await redis.aget(DISABLED_TENANT_KEY.format(tenant_id)):
            raise OpenApiTenantDisabledError()
    except OpenApiTenantDisabledError:
        raise
    except Exception:
        # Redis unavailable: fall through to the DB below. This is a security
        # check, so an infrastructure error must not turn into an allow.
        logger.warning("open_api: tenant status Redis check failed for tenant {}; falling back to DB", tenant_id)

    if tenant_id == DEFAULT_TENANT_ID:
        # Root tenant: `TenantService._guard_default_tenant` forbids disabling
        # it, and the middleware already checks the root blacklist. We still
        # honored the blacklist above; skip only the extra DB round-trip.
        return

    from bisheng.core.context.tenant import bypass_tenant_filter
    from bisheng.database.models.tenant import TenantDao

    with bypass_tenant_filter():
        tenant = await TenantDao.aget_by_id(tenant_id)
    if tenant is None or tenant.status != "active":
        raise OpenApiTenantDisabledError()


async def _guest_access_enabled() -> bool:
    """平台免登录总开关(DB 配置 `default_operator.enable_guest_access`)。

    与 3 个既有端点的读取口径一致(同样的键、同样每次读);`get_from_db` 自带
    100s 的 Redis 配置缓存,不需要再包一层。
    """
    conf = await settings.aget_from_db("default_operator")
    return bool(conf.get("enable_guest_access"))


async def _resolve_principal(
    conn: HTTPConnection,
    audit_ctx: dict | None = None,
) -> OpenApiPrincipal | None:
    """完成 ①-⑦ 的全部校验;返回 None 表示"走旧行为",否则返回可装载的主体。

    `audit_ctx` 是给调用方留的"口袋":③ 之后每一步失败(11805/11807/11816…)都属于
    **已被鉴权**的调用,AC-36 要求逐条留痕,而异常里拿不到 credential/account——
    所以这里边解析边把可审计字段塞进口袋,调用方在 except 分支里据此补记审计。
    """
    # ① 部署总闸(关 → 旧行为,AC-21)
    if not settings.open_api.enforce_keys:
        return None

    endpoint = conn.scope.get("endpoint")
    marker = get_open_api_scope_marker(endpoint)
    authorization = conn.headers.get("authorization")

    # ② 无名请求
    if authorization is None:
        # ②a 平台免登录面(guest 标记的 8 个端点,discovery 决策 D5):
        #     免登录开关开着 → 放行旧行为;关着 → 与其余端点一视同仁。
        if marker is not None and marker.guest and await _guest_access_enabled():
            return None
        # ②b 其余无名请求按默认租户的租户开关判定(决策 10)
        if await tenant_setting_service.is_enforced(DEFAULT_TENANT_ID):
            raise OpenApiKeyMissingError()
        return None

    # ③ 凭据校验(11801 / 11802 / 11803 / 11804)——未通过校验时还没有可归因的身份,
    # 不记调用审计(否则无效密钥扫描会把审计表灌爆);这些尝试只在服务端日志可见。
    credential = await validate_bearer(authorization)
    if audit_ctx is not None:
        audit_ctx["credential"] = credential

    # ④ 账号活性(11805)
    account = await ServiceAccountDao.aget_by_id(credential.service_account_id)
    if account is None or not account.is_enabled:
        raise OpenApiAccountDisabledError()
    if audit_ctx is not None:
        audit_ctx["account"] = account

    # ④b 租户活性(11806)：中间件对带密钥请求看的是 ROOT 租户，必须由这里自查。
    await _assert_tenant_active(credential.tenant_id)

    # ⑤ 端点标记与权限位(11811 漏标 / 11807 缺位)
    if marker is None:
        logger.error("open_api: /api/v2 endpoint without scope marker: {}", endpoint)
        raise OpenApiEndpointUnregisteredError()
    if not is_scope_granted(marker.scope, set(credential.scopes or [])):
        # design §4.2:11807 回带缺失的权限位(data.required),对接方据此自助补齐。
        raise OpenApiScopeMissingError(**{"required": marker.scope})

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
        expires_at=credential.expires_at,
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


def _audit_call(
    conn: HTTPConnection,
    principal: OpenApiPrincipal,
    started: float,
    *,
    http_status_code: int,
    biz_code: int,
    reason: str | None = None,
) -> None:
    """把一次被鉴权的调用交给审计服务(只入队,不阻塞、不影响调用结果)。

    业务码来源说明(实测口径,见 T-27 侦察报告):成功路径拿不到响应体,按信封惯例记
    200;端点上抛的 118xx 用异常自身的 HttpStatus/code;校验错误与非 open-api 异常
    都是"HTTP 200 + body 业务码"的惯例,分别记 422 / 500。**失败鉴权**(11801/11802
    等)没有 principal,不写审计——否则无效密钥扫描会把审计表灌爆;那些尝试只在
    服务端日志可见(已记偏差)。
    """
    is_ws = isinstance(conn, WebSocket)
    marker = None
    try:
        marker = get_open_api_scope_marker(conn.scope.get("endpoint"))
    except Exception:  # pragma: no cover - marker 缺失不影响审计主体
        marker = None
    call_audit_service.record_call(
        tenant_id=principal.tenant_id,
        credential_id=principal.credential_id,
        service_account_id=principal.service_account_id,
        owner_user_id=principal.owner_user_id,
        owner_name=getattr(principal.owner_payload, "user_name", None),
        key_mask=principal.key_mask,
        scopes=list(principal.scopes),
        required_scope=getattr(marker, "scope", None),
        # WS 没有 HTTP 状态:记 0 并在 metadata 里标 transport(close code 在依赖里
        # 看不到,详见 design §5 坑 19)。
        http_status=http_status_code,
        biz_code=biz_code,
        duration_ms=int((time.perf_counter() - started) * 1000),
        ip_address=get_request_ip(conn) if hasattr(conn, "headers") else None,
        endpoint=conn.url.path if getattr(conn, "url", None) is not None else None,
        method="WS" if is_ws else getattr(conn, "method", None),
        reason=reason,
    )


def _audit_rejected_call(
    conn: HTTPConnection,
    audit_ctx: dict,
    started: float,
    exc: BaseErrorCode,
) -> None:
    """解析期被拒的**已鉴权**调用补记一条审计(AC-36)。

    没走到 ③ 的失败(11801/11802:凭据本身无效)没有可归因身份,不记——否则无效密钥
    扫描会把审计表灌爆,那些尝试只在服务端日志可见(design §5 坑 19)。
    """
    credential = audit_ctx.get("credential")
    if credential is None:
        return
    account = audit_ctx.get("account")
    try:
        call_audit_service.record_call(
            tenant_id=int(getattr(credential, "tenant_id", 0) or 0),
            credential_id=int(getattr(credential, "id", 0) or 0),
            service_account_id=int(getattr(credential, "service_account_id", 0) or 0),
            owner_user_id=int(getattr(account, "resource_owner_user_id", 0) or 0),
            key_mask=getattr(credential, "key_mask", ""),
            scopes=list(getattr(credential, "scopes", None) or []),
            http_status=int(getattr(exc, "HttpStatus", 0) or 0),
            biz_code=int(getattr(exc, "code", 500)),
            duration_ms=int((time.perf_counter() - started) * 1000),
            ip_address=get_request_ip(conn) if hasattr(conn, "headers") else None,
            endpoint=conn.url.path if getattr(conn, "url", None) is not None else None,
            method="WS" if isinstance(conn, WebSocket) else getattr(conn, "method", None),
            reason="open_api_error",
        )
    except Exception:  # 审计绝不能改变调用结果(AC-36)
        logger.opt(exception=True).warning("open_api: rejected-call audit enqueue failed")


async def verify_open_api_access(conn: HTTPConnection) -> AsyncGenerator[None, None]:
    """`/api/v2` 的路由级鉴权依赖(HTTP 与 WS 共用;见模块 docstring)。

    F076 T-27:这里同时是**逐调用审计**的唯一 choke point——被鉴权(解析出 principal)
    的调用在结束时入队一条 `open_api.call`。审计只做入队,写失败/队列满都不改变调用结果。
    """
    started = time.perf_counter()
    audit_ctx: dict = {}
    try:
        principal = await _resolve_principal(conn, audit_ctx)
    except BaseErrorCode as exc:
        # 已过 ③ 的失败(11805/11807/11811/11816/11817)是"被鉴权但被拒的调用",
        # AC-36 要求留痕;此处按口袋里的凭据/账号补记一条。
        _audit_rejected_call(conn, audit_ctx, started, exc)
        if isinstance(conn, WebSocket):
            # 预 accept 抛 1008 → 客户端见握手 403(design §4.3);reason 只带业务码,
            # 细节留在服务端日志,避免向未认证的对端泄露判断依据。
            raise WebSocketException(code=http_status.WS_1008_POLICY_VIOLATION, reason=str(exc.code)) from exc
        raise

    if principal is None:  # 旧行为分支:总闸关,或无名请求且租户未开启
        yield
        return

    # F076 T-29(AC-23):给这次请求打标——异常处理器据此把"资源不可见/不存在"的
    # legacy 码统一成 11809/404(防枚举)。只有密钥鉴权的调用会被打标,总闸关/无名
    # 请求的旧错误口径逐字不变(spec AC-21)。
    try:
        conn.scope["open_api_call"] = True
    except Exception:  # pragma: no cover - scope 不可写时不影响鉴权本身
        logger.opt(exception=True).warning("open_api: cannot mark scope for call-face mapping")

    tokens = _install_context(principal)
    # 结果按"客户端真正看到什么"记(实测三档:成功 200/200;OpenApiError 用真状态+业务码;
    # 其余异常在信封惯例下都是 HTTP 200 + body 业务码)。
    outcome_status, outcome_code, outcome_reason = 200, 200, None
    try:
        yield
    except BaseErrorCode as exc:
        outcome_status = int(getattr(exc, "HttpStatus", 0) or 200)
        outcome_code = int(getattr(exc, "code", 500))
        outcome_reason = "open_api_error"
        raise
    except RequestValidationError:
        outcome_status, outcome_code, outcome_reason = 200, 422, "request_validation"
        raise
    except HTTPException as exc:
        outcome_status, outcome_code, outcome_reason = 200, int(exc.status_code), "http_exception"
        raise
    except Exception:
        outcome_status, outcome_code, outcome_reason = 200, 500, "internal_error"
        raise
    finally:
        _reset_context(tokens)
        try:
            _audit_call(
                conn,
                principal,
                started,
                http_status_code=outcome_status,
                biz_code=outcome_code,
                reason=outcome_reason,
            )
        except Exception:  # 审计绝不能改变调用结果(AC-36)
            logger.opt(exception=True).warning("open_api: call audit enqueue failed")
