"""F076 数据访问对象:API 密钥(本任务)与服务账号(后续任务按需扩展)。

**两种读取姿态,务必区分**:

- **匿名校验路径**(`aget_by_hash`):调用方尚未证明身份,密钥摘要可能是任意租户的,
  因此必须 `bypass_tenant_filter()` 跨租户读取;命中后再以密钥自带的 `tenant_id`
  建立上下文(design §2 约束 3 / §5 坑 1)。这是本模块唯一合理的 bypass 用法。
- **管理面路径**(列表/编辑/吊销):服务账号与密钥是租户级配置,跨租户不可见
  (spec AC-07)。沿用 F061 的 `strict_tenant_filter()` 手法——全局监听器默认给子租户
  是 `tenant_id IN (child, ROOT)`,对"租户私有配置"过宽;收窄必须发生在产生实例
  的那次读取上(否则脏刷新的 UPDATE 不会再被检查)。

SELECT 结果要用 `_rows()` 解包:租户过滤器的 `do_orm_execute` 会把 SELECT 重包成
单元素 Row(F056 生产事故 pitfall 11),`row[0]` 在有/无过滤器时都正确。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from loguru import logger
from sqlalchemy import select, update

from bisheng.core.context.tenant import bypass_tenant_filter, strict_tenant_filter
from bisheng.core.database import get_async_db_session
from bisheng.open_api.domain.models.api_credential import ApiCredential
from bisheng.open_api.domain.models.service_account import ServiceAccount


def _rows(result) -> list:
    """Unwrap ORM results (see module docstring).

    租户过滤器的 `do_orm_execute` 会把 SELECT 重包成单元素 Row;不加过滤器时
    `session.execute(select(Entity))` 返回的也是 Row。**两种情况下 `row[0]`
    都是实体本身**——照抄 datasource DAO 的同名助手(F056 生产事故 pitfall 11)。
    """
    return [row[0] for row in result.all()]


class ApiCredentialDao:
    """DAO for `api_credential`; all methods are classmethods (house convention)."""

    @classmethod
    async def acreate(cls, credential: ApiCredential) -> ApiCredential:
        async with get_async_db_session() as session:
            session.add(credential)
            await session.commit()
            await session.refresh(credential)
            return credential

    @classmethod
    async def aget_by_hash(cls, token_hash: str) -> ApiCredential | None:
        """Anonymous lookup by digest — cross-tenant on purpose (see module docstring)."""
        with bypass_tenant_filter():
            async with get_async_db_session() as session:
                result = await session.execute(select(ApiCredential).where(ApiCredential.token_hash == token_hash))
                rows = _rows(result)
        return rows[0] if rows else None

    @classmethod
    async def aget_by_id(cls, credential_id: int, *, tenant_id: int) -> ApiCredential | None:
        """Management-side read, narrowed to the acting tenant."""
        with strict_tenant_filter():
            async with get_async_db_session() as session:
                result = await session.execute(
                    select(ApiCredential).where(
                        ApiCredential.id == credential_id,
                        ApiCredential.tenant_id == tenant_id,
                    )
                )
                rows = _rows(result)
        return rows[0] if rows else None

    @classmethod
    async def alist_by_account(cls, service_account_id: int, *, tenant_id: int) -> list[ApiCredential]:
        with strict_tenant_filter():
            async with get_async_db_session() as session:
                result = await session.execute(
                    select(ApiCredential)
                    .where(
                        ApiCredential.service_account_id == service_account_id,
                        ApiCredential.tenant_id == tenant_id,
                    )
                    .order_by(ApiCredential.id.desc())
                )
                return _rows(result)

    @classmethod
    async def aupdate_fields(cls, credential_id: int, fields: dict[str, Any], *, tenant_id: int) -> int:
        """Incremental field update. Returns the affected row count.

        The global listener filters SELECT only — a bulk UPDATE passes straight
        through, so the tenant condition is written by hand here (C3 pitfall).
        """
        if not fields:
            return 0
        fields = {**fields, "update_time": datetime.now()}
        async with get_async_db_session() as session:
            result = await session.execute(
                update(ApiCredential)
                .where(
                    ApiCredential.id == credential_id,
                    ApiCredential.tenant_id == tenant_id,
                )
                .values(**fields)
            )
            await session.commit()
            return result.rowcount or 0

    @classmethod
    async def arevoke(cls, credential_id: int, *, reason: str, tenant_id: int, now: datetime | None = None) -> int:
        return await cls.aupdate_fields(
            credential_id,
            {"revoked_at": now or datetime.now(), "revoke_reason": reason},
            tenant_id=tenant_id,
        )

    @classmethod
    async def arevoke_all_for_account(
        cls, service_account_id: int, *, reason: str, tenant_id: int, now: datetime | None = None
    ) -> int:
        """Revoke every still-active key of one account (spec AC-11 的"全量吊销")。

        Hand-written tenant condition (bulk UPDATE bypasses the listener).
        """
        moment = now or datetime.now()
        async with get_async_db_session() as session:
            result = await session.execute(
                update(ApiCredential)
                .where(
                    ApiCredential.service_account_id == service_account_id,
                    ApiCredential.tenant_id == tenant_id,
                    ApiCredential.revoked_at.is_(None),
                )
                .values(revoked_at=moment, revoke_reason=reason, update_time=moment)
            )
            await session.commit()
            return result.rowcount or 0

    @classmethod
    async def atouch_last_used(cls, credential_id: int, *, now: datetime | None = None) -> None:
        """Best-effort `last_used_at` write — failure is logged, never raised (spec AC-14).

        Deliberately NOT wrapped in a raise-on-failure path: a hot-path write
        must not be able to fail the call it is observing.
        """
        try:
            moment = now or datetime.now()
            async with get_async_db_session() as session:
                # tenant-agnostic by-design: the credential id came from the
                # validated hash, and revocation state is irrelevant to a
                # timestamp touch. WHERE tenant_id is intentionally omitted.
                await session.execute(
                    update(ApiCredential).where(ApiCredential.id == credential_id).values(last_used_at=moment)
                )
                await session.commit()
        except Exception as exc:
            logger.debug("open_api: touch last_used_at failed for cred={}: {}", credential_id, exc)


class ServiceAccountDao:
    """DAO for `service_account`(T-06 的账号活性校验用;管理面方法随 T-17 扩展)。"""

    @classmethod
    async def aget_by_id(cls, account_id: int) -> ServiceAccount | None:
        """Read by id WITHOUT tenant narrowing.

        Used by the credential validator: the account id comes from a credential
        row that was already resolved by digest, so the tenant is established by
        the credential itself; narrowing again would only re-apply a context that
        has not been set yet (design §4.1 ④)。
        """
        with bypass_tenant_filter():
            async with get_async_db_session() as session:
                result = await session.execute(select(ServiceAccount).where(ServiceAccount.id == account_id))
                rows = _rows(result)
        return rows[0] if rows else None
