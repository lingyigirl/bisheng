"""F076 租户级强制开关(T-16,spec AC-34 / design §2.10)。

**强制口径(实现期澄清,已回写 design §2.10)**:强制 = 部署级 `open_api.enforce_keys`
**且** 该请求可识别租户的租户级开关。但"哪一件事被强制"要说清:

- **无名请求**:受两个开关共同门控——部署级关了走旧行为;部署级开着时,无名请求
  识别不出租户,按**默认租户**(即旧路径的执行身份所在租户,`DEFAULT_TENANT_ID`)
  的开关判定:开着要求密钥(11801),关着放行旧行为(分租户灰度)。
- **带密钥的请求**:密钥一经呈现就**永远校验、永远按密钥身份执行**——不因租户
  开关关闭而回落默认操作员。否则会出现"带密钥反而拿到更高身份"的安全退化,
  而且 AC-21 的"忽略 Authorization"只在**部署总闸关闭**时成立(AC-21 的原文条件
  是"强制鉴权关闭",即总闸)。

策略行**缺行 = 未启用**(fail-closed);读取走 5 秒缓存,写后即清缓存,
与密钥缓存同一套"变更 ≤5 秒生效"的时延口径。
"""

from __future__ import annotations

from loguru import logger

from bisheng.common.services.config_service import settings
from bisheng.open_api.domain.models.dao import OpenApiTenantSettingDao
from bisheng.open_api.domain.models.open_api_tenant_setting import OpenApiTenantSetting

#: 策略缓存 TTL;与设计"状态变更 ≤5 秒生效"的上界一致。
POLICY_CACHE_TTL_SECONDS = 5
_CACHE_KEY_TMPL = "oapi:tenant:{tenant_id}:enforce"


def _deployment_enforced() -> bool:
    """部署总闸(独立函数便于测试注入)。"""
    return bool(settings.open_api.enforce_keys)


async def _get_redis():
    """与密钥缓存同款降级:Redis 不可用 = 直通查库,不阻塞也不放行。"""
    try:
        from bisheng.core.cache.redis_manager import get_redis_client

        return await get_redis_client()
    except Exception as exc:
        logger.debug("open_api: redis unavailable, tenant policy cache degraded: {}", exc)
        return None


def _cache_key(tenant_id: int) -> str:
    return _CACHE_KEY_TMPL.format(tenant_id=tenant_id)


async def _cache_get_policy(tenant_id: int) -> bool | None:
    """返回缓存的 enforce 值;None = 未命中(含 Redis 不可用)。"""
    redis = await _get_redis()
    if redis is None:
        return None
    try:
        value = await redis.aget(_cache_key(tenant_id))
    except Exception as exc:
        logger.debug("open_api: tenant policy cache read failed: {}", exc)
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, dict) and "enforce_keys" in value:
        return bool(value["enforce_keys"])
    return None


async def _cache_set_policy(tenant_id: int, enforce_keys: bool) -> None:
    redis = await _get_redis()
    if redis is None:
        return
    try:
        await redis.aset(_cache_key(tenant_id), enforce_keys, expiration=POLICY_CACHE_TTL_SECONDS)
    except Exception as exc:
        logger.debug("open_api: tenant policy cache write failed: {}", exc)


async def _cache_clear_policy(tenant_id: int) -> None:
    redis = await _get_redis()
    if redis is None:
        return
    try:
        await redis.adelete(_cache_key(tenant_id))
    except Exception as exc:
        logger.debug("open_api: tenant policy cache delete failed (TTL still bounds it): {}", exc)


async def tenant_enforced(tenant_id: int) -> bool:
    """租户级开关(不含部署级);缺行 = 未启用。"""
    cached = await _cache_get_policy(tenant_id)
    if cached is not None:
        return cached

    row = await OpenApiTenantSettingDao.aget(tenant_id)
    value = bool(row.enforce_keys) if row is not None else False
    await _cache_set_policy(tenant_id, value)
    return value


async def is_enforced(tenant_id: int) -> bool:
    """强制判定 = 部署级 ∧ 租户级(缺行即关)。"""
    if not _deployment_enforced():
        return False
    return await tenant_enforced(tenant_id)


async def get_policy(tenant_id: int) -> OpenApiTenantSetting | None:
    """管理面读取原始策略行(不经过强制判定)。"""
    return await OpenApiTenantSettingDao.aget(tenant_id)


async def upsert_policy(tenant_id: int, *, enforce_keys: bool) -> OpenApiTenantSetting:
    """管理面写入并**立即清缓存**——变更 ≤5 秒生效(spec AC-34)。"""
    row = await OpenApiTenantSettingDao.aupsert(tenant_id, enforce_keys=enforce_keys)
    await _cache_clear_policy(tenant_id)
    return row
