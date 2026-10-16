"""F076 开放 API 的领域常量。

审计动作名放这里(与 `TenantAuditAction` / `DocExtractAuditAction` 同约定):
裸字符串拼错会**行照样写入、但查询永远命中不了**且不报错,所以集中成枚举。
动作名前缀由 design §6.1 固定,新增动作必须落在这几棵树里:
`open_api.call` / `open_api.service_account.*` / `open_api.api_key.*` / `open_api.settings.update`。
"""

from __future__ import annotations

from enum import StrEnum


class OpenApiAuditAction(StrEnum):
    """审计动作词表(design §6.1 的动作名清单)。"""

    #: 逐调用审计(/api/v2,AC-36)。
    CALL = "open_api.call"
    #: 服务账号管理动作(AC-37)。
    SERVICE_ACCOUNT_CREATE = "open_api.service_account.create"
    SERVICE_ACCOUNT_UPDATE = "open_api.service_account.update"
    SERVICE_ACCOUNT_ENABLE = "open_api.service_account.enable"
    SERVICE_ACCOUNT_DISABLE = "open_api.service_account.disable"
    SERVICE_ACCOUNT_DELETE = "open_api.service_account.delete"
    #: 代管资源授权变更(AC-37 的"资源授权变更")。挂在 service_account 树下,
    #: 不新开前缀——design §6.1 只固定了四棵树。
    SERVICE_ACCOUNT_GRANT_MUTATE = "open_api.service_account.grant_mutate"
    #: 密钥管理动作(AC-37 / AC-15)。
    API_KEY_ISSUE = "open_api.api_key.issue"
    API_KEY_UPDATE = "open_api.api_key.update"
    API_KEY_REVOKE = "open_api.api_key.revoke"
    API_KEY_REVOKE_ALL = "open_api.api_key.revoke_all"
    #: 租户强制开关变更(AC-37 的"租户开关变更")。
    SETTINGS_UPDATE = "open_api.settings.update"
