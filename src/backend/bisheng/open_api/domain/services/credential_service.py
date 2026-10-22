"""F076 密钥签发 / 编辑 / 吊销 + 正缓存(T-05)。

- **明文只出现一次**:`issue_credential()` 的返回值里带回明文,库内只存
  SHA-256 摘要(INV-23 条款 1)。任何其他接口/日志/审计都不得出现明文。
- **正缓存**(`oapi:cred:{digest}`)把"每请求一次库读"降为"每 TTL 一次";
  吊销 / 编辑 / 全量吊销必须**主动清缓存**,否则变更要等 TTL 才生效——
  spec AC-11 / AC-13 的 5 秒上界就是 TTL 上界(`OpenApiConf` 里 `le=5`)。
- **缓存是优化不是权威**:Redis 不可用时降级为直接查库(debug 日志),既不
  因此放行也不因此拒绝;权威存储不可用时由依赖层转 503/11812(fail-closed)。
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
from datetime import datetime

from cryptography.fernet import InvalidToken
from loguru import logger

from bisheng.common.errcode.open_api import OpenApiKeyNotRevealableError
from bisheng.common.services.config_service import settings
from bisheng.core.config.settings import decrypt_token, encrypt_token
from bisheng.open_api.domain.models.api_credential import (
    KEY_SECRET_LENGTH,
    REVOKE_REASON_BATCH,
    REVOKE_REASON_MANUAL,
    SERVICE_ACCOUNT_KEY_PREFIX,
    ApiCredential,
)
from bisheng.open_api.domain.models.dao import ApiCredentialDao

#: 缓存键前缀;键尾是明文摘要(不是明文本身)。
#: v2 (2026-10-22): payload 新增 allowed_apps;旧条目缺少该字段会被当作"不限制",
#: 因此升版前缀,避免部署后 ≤TTL 的窗口内出现权限放大。
CACHE_KEY_PREFIX = "oapi:cred:v2:"

#: 管理面允许被编辑的字段白名单(spec AC-13 / AC-43)。
EDITABLE_FIELDS = frozenset({"name", "scopes", "expires_at", "allowed_apps"})


def hash_token(plaintext: str) -> str:
    """SHA-256 hex digest —— 库里存的唯一形态(spec AC-09)。"""
    return hashlib.sha256(plaintext.encode()).hexdigest()


def _generate_plaintext() -> str:
    """`bs-sak-` + `secrets.token_urlsafe(32)`;长度不符即抛错(绝不静默签发短密钥)。"""
    secret = secrets.token_urlsafe(32)
    if len(secret) != KEY_SECRET_LENGTH:
        raise RuntimeError(f"open_api: unexpected token_urlsafe length {len(secret)} (expected {KEY_SECRET_LENGTH})")
    return f"{SERVICE_ACCOUNT_KEY_PREFIX}{secret}"


def _cache_key(digest: str) -> str:
    return f"{CACHE_KEY_PREFIX}{digest}"


async def _get_redis():
    """Redis 句柄;不可用返回 None(缓存降级为 miss,绝不阻塞请求)。"""
    try:
        from bisheng.core.cache.redis_manager import get_redis_client

        return await get_redis_client()
    except Exception as exc:
        logger.debug("open_api: redis unavailable, credential cache degraded to pass-through: {}", exc)
        return None


def _credential_cache_ttl() -> int:
    return settings.open_api.credential_cache_ttl_seconds


def to_cache_payload(row: ApiCredential) -> dict:
    """把凭据行序列化成可 pickle 的纯字典(datetime → isoformat)。"""
    return {
        "id": row.id,
        "tenant_id": row.tenant_id,
        "service_account_id": row.service_account_id,
        "name": row.name,
        "key_prefix": row.key_prefix,
        "last4": row.last4,
        "token_hash": row.token_hash,
        "scopes": list(row.scopes or []),
        # Keep None vs [] intact: None = unrestricted, [] = deny all agents.
        "allowed_apps": list(row.allowed_apps) if row.allowed_apps is not None else None,
        "expires_at": row.expires_at.isoformat() if row.expires_at else None,
        "revoked_at": row.revoked_at.isoformat() if row.revoked_at else None,
        "revoke_reason": row.revoke_reason,
    }


def from_cache_payload(payload: dict) -> ApiCredential:
    """缓存字典 → 游离态 ApiCredential(不附着 session,仅承载校验所需字段)。"""

    def _dt(value: str | None) -> datetime | None:
        return datetime.fromisoformat(value) if value else None

    return ApiCredential(
        id=payload["id"],
        tenant_id=payload["tenant_id"],
        service_account_id=payload["service_account_id"],
        name=payload.get("name"),
        key_prefix=payload.get("key_prefix", SERVICE_ACCOUNT_KEY_PREFIX),
        last4=payload.get("last4", ""),
        token_hash=payload["token_hash"],
        scopes=payload.get("scopes") or [],
        # Do NOT use `or None`: an explicit [] must survive the cache round-trip.
        allowed_apps=payload.get("allowed_apps"),
        expires_at=_dt(payload.get("expires_at")),
        revoked_at=_dt(payload.get("revoked_at")),
        revoke_reason=payload.get("revoke_reason"),
    )


async def cache_get_credential(digest: str) -> ApiCredential | None:
    redis = await _get_redis()
    if redis is None:
        return None
    try:
        payload = await redis.aget(_cache_key(digest))
    except Exception as exc:
        logger.debug("open_api: credential cache read failed: {}", exc)
        return None
    if not isinstance(payload, dict):
        return None
    try:
        return from_cache_payload(payload)
    except Exception as exc:
        logger.debug("open_api: malformed credential cache entry, treating as miss: {}", exc)
        return None


async def cache_set_credential(row: ApiCredential) -> None:
    if row.token_hash is None:
        return
    redis = await _get_redis()
    if redis is None:
        return
    try:
        await redis.aset(_cache_key(row.token_hash), to_cache_payload(row), expiration=_credential_cache_ttl())
    except Exception as exc:
        logger.debug("open_api: credential cache write failed: {}", exc)


async def cache_delete_credential(digest: str) -> None:
    """吊销 / 编辑后立即失效——把"≤5 秒生效"从 TTL 等待降为即时。"""
    redis = await _get_redis()
    if redis is None:
        return
    try:
        await redis.adelete(_cache_key(digest))
    except Exception as exc:
        logger.debug("open_api: credential cache delete failed (TTL still bounds the lag): {}", exc)


async def issue_credential(
    *,
    tenant_id: int,
    service_account_id: int,
    name: str | None = None,
    scopes: list[str] | None = None,
    allowed_apps: list[dict] | None = None,
    expires_at: datetime | None = None,
    created_by: int | None = None,
) -> tuple[ApiCredential, str]:
    """签发一把密钥,返回 `(行, 明文)`。

    明文同时写入不可逆摘要(校验用)与 Fernet 密文(管理员查看用)。调用方可以
    在签发响应里返回明文,但不得把它写入日志/审计/缓存。
    到期语义:`expires_at=None` 表示不过期(对齐上游口径,签发表单给预设)。
    `allowed_apps`: None = 不限制;[] = 全拒绝;非空 = 白名单。
    """
    plaintext = _generate_plaintext()
    row = ApiCredential(
        tenant_id=tenant_id,
        service_account_id=service_account_id,
        name=name,
        key_prefix=SERVICE_ACCOUNT_KEY_PREFIX,
        last4=plaintext[-4:],
        token_hash=hash_token(plaintext),
        token_ciphertext=encrypt_token(plaintext).decode(),
        scopes=list(scopes or []),
        allowed_apps=list(allowed_apps) if allowed_apps is not None else None,
        expires_at=expires_at,
        created_by=created_by,
    )
    created = await ApiCredentialDao.acreate(row)
    return created, plaintext


def reveal_credential(row: ApiCredential) -> str:
    """Decrypt a key for the explicit admin reveal action (spec AC-44).

    Raises 11823 when the row predates the reveal feature (no ciphertext) or the
    ciphertext cannot be decrypted (secret rotated / corrupted). The decrypted
    value is verified against the stored hash before it is returned, so a
    mismatched ciphertext can never surface as a working key.
    """
    if not row.token_ciphertext:
        raise OpenApiKeyNotRevealableError()
    try:
        plaintext = decrypt_token(row.token_ciphertext)
    except (InvalidToken, TypeError, ValueError):
        # Never log the ciphertext or the plaintext; the credential id is enough.
        logger.opt(exception=True).warning("open_api: credential {} ciphertext cannot be decrypted", row.id)
        raise OpenApiKeyNotRevealableError()
    if not hmac.compare_digest(hash_token(plaintext), row.token_hash):
        logger.error("open_api: credential {} ciphertext does not match its hash", row.id)
        raise OpenApiKeyNotRevealableError()
    return plaintext


async def update_credential(credential_id: int, fields: dict, *, tenant_id: int) -> bool:
    """编辑名称 / 权限位 / 智能体白名单 / 有效期(spec AC-13 / AC-43:立即生效,无需重新签发)。

    只接受白名单字段;生效靠"改库 + 清缓存",下一请求即按新值校验。
    """
    unknown = set(fields) - EDITABLE_FIELDS
    if unknown:
        raise ValueError(f"open_api: non-editable credential fields: {sorted(unknown)}")

    row = await ApiCredentialDao.aget_by_id(credential_id, tenant_id=tenant_id)
    if row is None:
        return False

    changed = await ApiCredentialDao.aupdate_fields(credential_id, dict(fields), tenant_id=tenant_id)
    if changed:
        await cache_delete_credential(row.token_hash)
    return bool(changed)


async def revoke_credential(credential_id: int, *, tenant_id: int, reason: str = REVOKE_REASON_MANUAL) -> bool:
    """吊销单把密钥(吊销 ≠ 删除,记录保留供审计,spec AC-11)。"""
    row = await ApiCredentialDao.aget_by_id(credential_id, tenant_id=tenant_id)
    if row is None:
        return False
    changed = await ApiCredentialDao.arevoke(credential_id, reason=reason, tenant_id=tenant_id)
    if changed:
        await cache_delete_credential(row.token_hash)
    return bool(changed)


async def revoke_account_credentials(
    service_account_id: int, *, tenant_id: int, reason: str = REVOKE_REASON_BATCH
) -> int:
    """全量吊销某账号名下所有未吊销密钥,并逐把清缓存。

    被服务账号停用 / 删除路径复用(`subject_disabled` / `subject_deleted`)。
    """
    rows = await ApiCredentialDao.alist_by_account(service_account_id, tenant_id=tenant_id)
    active = [r for r in rows if r.revoked_at is None]
    changed = await ApiCredentialDao.arevoke_all_for_account(service_account_id, reason=reason, tenant_id=tenant_id)
    for row in active:
        await cache_delete_credential(row.token_hash)
    return changed
