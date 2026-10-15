"""F076 开放 API 鉴权模块错误码,模块编码 118。

分配前已按 v2.7.0 release-contract 的三来源口径扫描确认全空
(`common/errcode/*.py` / 全部版本 release-contract / `packages/locales/src/api_errors/*.json`);
118 此前由 F062 / F063 / F064 连续三次声明「留待后续 Feature」,由本特性正式申领
(11801-11817,见 release-contract 的「已分配模块编码」表)。

**与全局错误码的两点差异(design §3 决策 3 / §4.2)**:

1. 本模块每个类额外声明 `HttpStatus` —— 开放 API 面向行内系统,鉴权失败必须回
   **真实 HTTP 状态码**(401/403/404/400/500/503);`BaseErrorCode.http_exception()`
   会把业务码直接当 HTTP 状态(本仓历史约定),故 `/api/v2` 使用专用异常处理器读
   `HttpStatus`(T-13 落地),不复用 `http_exception()`。
2. 所有 Msg 用英文;中文 / 日文文案在 `packages/locales/src/api_errors/` 的三语源文件
   里以码号对账(`pnpm check-i18n`),前端按 `api_errors:<code>` 取本地化文案。
"""

from bisheng.common.errcode.base import BaseErrorCode


class OpenApiErrorBase(BaseErrorCode):
    """F076 错误基类:携带真实 HTTP 状态码(见模块 docstring 第 1 条)。

    公开命名:_EXCEPTION_HANDLERS 在 main.py 里按本类注册专用处理器
    (T-13),Starlette 沿 MRO 匹配,因此全部 17 个子类自动走真状态 + 信封。
    """

    HttpStatus: int = 400


class OpenApiKeyMissingError(OpenApiErrorBase):
    """请求未携带密钥,或密钥格式与 `bs-sak-<43 字符>` 不符(spec AC-17)。

    格式非法与缺失共用一码:两者都在"凭据未通过解析"这一层,区分只会告诉
    探测者哪一半做对了。
    """

    Code: int = 11801
    Msg: str = "Missing API key or malformed key"
    HttpStatus: int = 401


class OpenApiKeyInvalidError(OpenApiErrorBase):
    """摘要查不到对应密钥(含已物理清理的历史密钥,spec AC-18)。"""

    Code: int = 11802
    Msg: str = "Invalid API key"
    HttpStatus: int = 401


class OpenApiKeyExpiredError(OpenApiErrorBase):
    """密钥已超过有效期(过期判定排他:`expires_at > now` 才有效,spec AC-12)。"""

    Code: int = 11803
    Msg: str = "API key has expired"
    HttpStatus: int = 401


class OpenApiKeyRevokedError(OpenApiErrorBase):
    """密钥已被吊销(吊销 ≠ 删除,记录保留供审计,spec AC-11)。"""

    Code: int = 11804
    Msg: str = "API key has been revoked"
    HttpStatus: int = 401


class OpenApiAccountDisabledError(OpenApiErrorBase):
    """密钥所属服务账号已停用或已删除(spec AC-05 / AC-06)。"""

    Code: int = 11805
    Msg: str = "The service account is disabled"
    HttpStatus: int = 401


class OpenApiTenantDisabledError(OpenApiErrorBase):
    """密钥所属租户已停用——必须由鉴权依赖自查(中间件的租户停用检查此时看的是 ROOT 租户,见 design §5 坑 1)。"""

    Code: int = 11806
    Msg: str = "The tenant is disabled"
    HttpStatus: int = 403


class OpenApiScopeMissingError(OpenApiErrorBase):
    """密钥缺少该端点所需权限位(spec AC-22);响应 `data.required` 回带缺失的权限位。"""

    Code: int = 11807
    Msg: str = "Missing the scope required by this endpoint"
    HttpStatus: int = 403


class OpenApiResourceNotGrantedError(OpenApiErrorBase):
    """调用方对目标资源**已知可见但授权档位不足**(例如写操作需要 editor 而密钥只授了 viewer)。

    与 11809 的分工:资源存在性本身敏感、需要防枚举时用 11809;此处用于
    资源对调用方本就可见、仅操作权限不够的情形(spec AC-06a 的授权边界)。
    """

    Code: int = 11808
    Msg: str = "You do not have permission to access this resource"
    HttpStatus: int = 403


class OpenApiNotFoundError(OpenApiErrorBase):
    """资源不存在**或**不在该服务账号的授权范围内——两者不可区分(防枚举,spec AC-23)。"""

    Code: int = 11809
    Msg: str = "Resource not found or not accessible"
    HttpStatus: int = 404


class OpenApiOwnerNotAllowedError(OpenApiErrorBase):
    """创建 / 变更服务账号时,其资源所有者是管理员(spec AC-03,防提权第一重)。"""

    Code: int = 11810
    Msg: str = "A service account's resource owner must not be an administrator"
    HttpStatus: int = 400


class OpenApiEndpointUnregisteredError(OpenApiErrorBase):
    """端点没有登记所需权限位——服务端配置错误(fail-closed,spec AC-27)。

    对调用方而言这是 500 而非 403:不是"你无权",而是"平台漏标了"。
    """

    Code: int = 11811
    Msg: str = "Endpoint has no scope registered; the call was rejected"
    HttpStatus: int = 500


class OpenApiAuthBackendUnavailableError(OpenApiErrorBase):
    """鉴权依赖(Redis / 数据库)不可用——fail-closed,绝不放行(spec AC-24)。"""

    Code: int = 11812
    Msg: str = "Authentication service is temporarily unavailable; please retry later"
    HttpStatus: int = 503


class OpenApiAccountNameDuplicateError(OpenApiErrorBase):
    """同一租户内服务账号重名(唯一约束 `uk_service_account_tenant_name`,spec AC-01)。"""

    Code: int = 11813
    Msg: str = "A service account with this name already exists"
    HttpStatus: int = 400


class OpenApiResourceGrantsUnavailableError(OpenApiErrorBase):
    """资源授权服务不可用(管理面授权变更 / 依赖校验)——fail-closed(spec AC-06a / AC-24)。"""

    Code: int = 11814
    Msg: str = "Authorization service is temporarily unavailable; please retry later"
    HttpStatus: int = 503


class OpenApiToolNotAllowedError(OpenApiErrorBase):
    """`tools[]` 指定了未绑定 / 未启用 / 不可识别的工具(spec AC-30,INV-24);`data.tool_key` 回带工具标识。"""

    Code: int = 11815
    Msg: str = "The specified tool is unavailable or not bound to this application"
    HttpStatus: int = 403


class OpenApiOwnerPrivilegedError(OpenApiErrorBase):
    """运行时发现服务账号的资源所有者已被提权为管理员——拒绝调用(spec AC-04,防提权第二重)。"""

    Code: int = 11816
    Msg: str = "The service account's resource owner now holds administrator privileges; the call was rejected"
    HttpStatus: int = 403


class OpenApiToolScopeNotApplicableError(OpenApiErrorBase):
    """`tools[]` 在本次调用形态下无法生效(如工作流处于"等待输入"的续跑分支--
    该分支不再写执行数据,override 无处施加)。

    INV-24 要求"不可识别/不可生效"必须显式报错而不是静默忽略:调用方以为
    限定了本轮工具、实际没限定,是比 403 更危险的静默语义漂移。
    """

    Code: int = 11818
    Msg: str = "tools is not applicable to this call"
    HttpStatus: int = 400


class OpenApiIdentityInputRejectedError(OpenApiErrorBase):
    """请求中携带调用方指定的裸 `user_id`——身份不可由调用方指定(spec AC-25,INV-23 条款 3)。"""

    Code: int = 11817
    Msg: str = "Specifying a user identity in the request is not allowed"
    HttpStatus: int = 400
