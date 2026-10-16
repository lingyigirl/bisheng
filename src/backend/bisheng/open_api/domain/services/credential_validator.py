"""F076 密钥校验(T-05):`Authorization: Bearer` → `ApiCredential`。

校验顺序(对齐上游同类实现):**格式 → 登记(摘要命中)→ 撤销 / 过期**,
每一步对应独立错误码,让对接方能自助定位(spec AC-17 / AC-18):

| 情形 | 错误码 | HTTP |
|---|---|---|
| 缺失 / 格式非法 | 11801 | 401 |
| 摘要查不到 | 11802 | 401 |
| 已吊销 | 11804 | 401 |
| 已过期 | 11803 | 401 |

本模块**不**判定账号停用(11805)/ 租户停用(11806)/ 权限位(11807)——
那些需要租户上下文与 scope 注册表,由鉴权依赖(T-06)在拿到凭据后完成。
"""

from __future__ import annotations

import hmac
import re
from datetime import datetime

from bisheng.common.errcode.open_api import (
    OpenApiKeyExpiredError,
    OpenApiKeyInvalidError,
    OpenApiKeyMissingError,
    OpenApiKeyRevokedError,
)
from bisheng.open_api.domain.models.api_credential import (
    KEY_SECRET_LENGTH,
    SERVICE_ACCOUNT_KEY_PREFIX,
    ApiCredential,
)
from bisheng.open_api.domain.models.dao import ApiCredentialDao
from bisheng.open_api.domain.services.credential_service import (
    cache_get_credential,
    cache_set_credential,
    hash_token,
)

#: 前缀与主体类型绑死:未知前缀直接拒绝,不做兼容猜测(design §3 决策 5)。
_KEY_PATTERN = re.compile(rf"^{re.escape(SERVICE_ACCOUNT_KEY_PREFIX)}[A-Za-z0-9_-]{{{KEY_SECRET_LENGTH}}}$")


def extract_bearer_token(authorization: str | None) -> str:
    """从 `Authorization` 头取出密钥明文;缺失或格式非法一律 11801。

    格式非法与缺失共用一码:区分只会告诉探测者哪一半做对了。
    """
    if not authorization:
        raise OpenApiKeyMissingError()
    parts = authorization.split(None, 1)
    if len(parts) != 2 or parts[0].lower() != "bearer":
        raise OpenApiKeyMissingError()
    token = parts[1].strip()
    if not _KEY_PATTERN.match(token):
        raise OpenApiKeyMissingError()
    return token


async def validate_bearer(authorization: str | None, *, now: datetime | None = None) -> ApiCredential:
    """校验 Bearer 密钥,返回凭据行(游离态——不附着 session,调用方勿直接改)。

    缓存命中与 DB 命中走同一条后续校验路径(撤销/过期判定在缓存值上同样成立,
    因为吊销时会主动清缓存,TTL 上限 5 秒封顶任何残余滞后)。
    """
    moment = now or datetime.now()
    token = extract_bearer_token(authorization)
    digest = hash_token(token)

    row = await cache_get_credential(digest)
    from_cache = row is not None
    if row is None:
        row = await ApiCredentialDao.aget_by_hash(digest)

    if row is None:
        raise OpenApiKeyInvalidError()

    # 防时序的二次比对:查库本已按摘要等值命中,这里再常量时间确认一次,
    # 避免"先算摘要再比较"的实现差异被计时侧信道利用。
    if not hmac.compare_digest(row.token_hash, digest):
        raise OpenApiKeyInvalidError()

    if row.revoked_at is not None:
        raise OpenApiKeyRevokedError()

    if not row.is_valid_at(moment):
        raise OpenApiKeyExpiredError()

    if not from_cache:
        # 只在缓存未命中时回填 + 触达 last_used_at —— 把"每请求一次写"压成
        # "每 TTL 一次",这就是 spec AC-14 允许的合并写入。
        await cache_set_credential(row)
        await ApiCredentialDao.atouch_last_used(row.id, now=moment)

    return row
