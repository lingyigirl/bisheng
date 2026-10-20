"""F078 数据访问对象：纳管注册表 / 分类法 / 调用留痕。

**读取姿态**：

- 管理面与客户端目录：租户级私有，必须 ``strict_tenant_filter()``（子租户默认
  可见集是 ``{child, ROOT}``，不能只用自动过滤；F076/F058 先例）。
- 调度/健康扫描：跨租户，调用方显式 ``bypass_tenant_filter()`` 后再按行
  ``set_current_tenant_id``；本 DAO 的 ``*_bypass`` 方法只做查询，不替调用方
  做租户判定。

**写姿态**：所有状态迁移都是 ``UPDATE ... WHERE id=? AND tenant_id=?`` +
``rowcount`` CAS；bulk UPDATE 不吃租户自动过滤，故手写 ``tenant_id``；禁
``RETURNING``（DM8 多行 RETURNING 报 ``[CODE:-5016]``）。

回滚：三张表均可 ``DROP TABLE``；无 Go 侧或既有表依赖。
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import delete, func, select, update
from sqlalchemy.sql import case

from bisheng.core.context.tenant import bypass_tenant_filter, strict_tenant_filter
from bisheng.core.database import get_async_db_session
from bisheng.managed_agent.domain.models.managed_agent import (
    AgentStatus,
    ManagedAgent,
)
from bisheng.managed_agent.domain.models.run import ManagedAgentRun, ManagedRunStatus
from bisheng.managed_agent.domain.models.taxonomy import ManagedAgentTaxonomy


class ManagedAgentDao:
    """DAO for ``managed_agent``; classmethods per house convention."""

    # ── 基础 CRUD ──────────────────────────────────────────────────────

    @classmethod
    async def acreate(cls, agent: ManagedAgent) -> ManagedAgent:
        async with get_async_db_session() as session:
            session.add(agent)
            await session.commit()
            await session.refresh(agent)
            return agent

    @classmethod
    async def aget(cls, agent_id: int) -> ManagedAgent | None:
        """管理面读取：严格租户过滤（跨租户/ROOT 行都读不到）。"""
        with strict_tenant_filter():
            async with get_async_db_session() as session:
                result = await session.execute(select(ManagedAgent).where(ManagedAgent.id == agent_id))
                return result.scalars().first()

    @classmethod
    async def aget_bypass(cls, agent_id: int) -> ManagedAgent | None:
        """跨租户读取（调度/健康扫描）；调用方必须自行校验 tenant_id。"""
        with bypass_tenant_filter():
            async with get_async_db_session() as session:
                result = await session.execute(select(ManagedAgent).where(ManagedAgent.id == agent_id))
                return result.scalars().first()

    @classmethod
    async def aget_by_name(cls, name: str) -> ManagedAgent | None:
        with strict_tenant_filter():
            async with get_async_db_session() as session:
                result = await session.execute(select(ManagedAgent).where(ManagedAgent.name == name))
                return result.scalars().first()

    @classmethod
    async def aget_by_endpoint_hash(cls, endpoint_hash: str) -> ManagedAgent | None:
        with strict_tenant_filter():
            async with get_async_db_session() as session:
                result = await session.execute(select(ManagedAgent).where(ManagedAgent.endpoint_hash == endpoint_hash))
                return result.scalars().first()

    @classmethod
    async def alist(
        cls,
        *,
        keyword: str | None = None,
        source_kind: str | None = None,
        protocol: str | None = None,
        status: str | None = None,
        scenario_code: str | None = None,
        industry_code: str | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[list[ManagedAgent], int]:
        """管理面/客户端目录列表；严格租户过滤 + 结构化筛选。"""
        conditions = []
        if keyword:
            conditions.append(ManagedAgent.name.like(f"%{keyword}%"))
        if source_kind:
            conditions.append(ManagedAgent.source_kind == source_kind)
        if protocol:
            conditions.append(ManagedAgent.protocol == protocol)
        if status:
            conditions.append(ManagedAgent.status == status)
        if scenario_code:
            conditions.append(ManagedAgent.scenario_code == scenario_code)
        if industry_code:
            conditions.append(ManagedAgent.industry_code == industry_code)

        with strict_tenant_filter():
            async with get_async_db_session() as session:
                total = (await session.execute(select(func.count(ManagedAgent.id)).where(*conditions))).scalar() or 0
                result = await session.execute(
                    select(ManagedAgent)
                    .where(*conditions)
                    .order_by(ManagedAgent.create_time.desc())
                    .offset(max(page - 1, 0) * page_size)
                    .limit(page_size)
                )
                return list(result.scalars().all()), int(total)

    @classmethod
    async def alist_online_for_catalog(
        cls,
        *,
        keyword: str | None = None,
        scenario_code: str | None = None,
        industry_code: str | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[list[ManagedAgent], int]:
        """客户端目录：只返回 online；权限过滤由 service 层逐行判定。"""
        return await cls.alist(
            keyword=keyword,
            status=AgentStatus.ONLINE.value,
            scenario_code=scenario_code,
            industry_code=industry_code,
            page=page,
            page_size=page_size,
        )

    @classmethod
    async def acount_by_tenant(cls) -> int:
        with strict_tenant_filter():
            async with get_async_db_session() as session:
                return int((await session.execute(select(func.count(ManagedAgent.id)))).scalar() or 0)

    @classmethod
    async def alist_health_scan(cls, *, limit: int = 500) -> list[ManagedAgent]:
        """Cross-tenant beat scan: online agents, least-recently-checked first."""
        with bypass_tenant_filter():
            async with get_async_db_session() as session:
                result = await session.execute(
                    select(ManagedAgent)
                    .where(ManagedAgent.status == AgentStatus.ONLINE.value)
                    .order_by(ManagedAgent.last_health_check_at.asc())
                    .limit(limit)
                )
                return list(result.scalars().all())

    @classmethod
    async def acount_by_taxonomy(cls, kind: str, code: str) -> int:
        """How many agents still reference a scenario/industry code (delete guard)."""
        column = ManagedAgent.scenario_code if kind == "scenario" else ManagedAgent.industry_code
        with strict_tenant_filter():
            async with get_async_db_session() as session:
                return int(
                    (await session.execute(select(func.count(ManagedAgent.id)).where(column == code))).scalar() or 0
                )

    # ── 写入（CAS / 手写租户） ─────────────────────────────────────────

    @classmethod
    async def aupdate_fields(cls, agent_id: int, fields: dict, *, tenant_id: int) -> int:
        if not fields:
            return 0
        fields = {**fields, "update_time": datetime.now()}
        async with get_async_db_session() as session:
            result = await session.execute(
                update(ManagedAgent)
                .where(ManagedAgent.id == agent_id, ManagedAgent.tenant_id == tenant_id)
                .values(**fields)
            )
            await session.commit()
            return result.rowcount or 0

    @classmethod
    async def atransition(
        cls,
        agent_id: int,
        *,
        tenant_id: int,
        from_status: str,
        to_status: str,
        extra: dict | None = None,
    ) -> int:
        """状态机 CAS：只有当前状态等于 ``from_status`` 才迁移。"""
        values = {"status": to_status, "update_time": datetime.now()}
        if extra:
            values.update(extra)
        async with get_async_db_session() as session:
            result = await session.execute(
                update(ManagedAgent)
                .where(
                    ManagedAgent.id == agent_id,
                    ManagedAgent.tenant_id == tenant_id,
                    ManagedAgent.status == from_status,
                )
                .values(**values)
            )
            await session.commit()
            return result.rowcount or 0

    @classmethod
    async def adelete(cls, agent_id: int, *, tenant_id: int) -> int:
        """硬删除；调用方先检查启用中的定时任务（T-11）。"""
        async with get_async_db_session() as session:
            result = await session.execute(
                delete(ManagedAgent).where(
                    ManagedAgent.id == agent_id,
                    ManagedAgent.tenant_id == tenant_id,
                )
            )
            await session.commit()
            return result.rowcount or 0

    @classmethod
    async def aupdate_health(cls, agent_id: int, fields: dict, *, tenant_id: int) -> int:
        return await cls.aupdate_fields(agent_id, fields, tenant_id=tenant_id)

    @classmethod
    async def aupdate_last_call(
        cls,
        agent_id: int,
        *,
        tenant_id: int,
        status: str,
        latency_ms: int | None,
        at: datetime | None = None,
    ) -> int:
        return await cls.aupdate_fields(
            agent_id,
            {
                "last_call_at": at or datetime.now(),
                "last_run_status": status,
                "last_latency_ms": latency_ms,
            },
            tenant_id=tenant_id,
        )


class ManagedAgentTaxonomyDao:
    """DAO for ``managed_agent_taxonomy``."""

    @classmethod
    async def acreate(cls, row: ManagedAgentTaxonomy) -> ManagedAgentTaxonomy:
        async with get_async_db_session() as session:
            session.add(row)
            await session.commit()
            await session.refresh(row)
            return row

    @classmethod
    async def aget(cls, row_id: int) -> ManagedAgentTaxonomy | None:
        with strict_tenant_filter():
            async with get_async_db_session() as session:
                result = await session.execute(select(ManagedAgentTaxonomy).where(ManagedAgentTaxonomy.id == row_id))
                return result.scalars().first()

    @classmethod
    async def aget_by_code(cls, kind: str, code: str) -> ManagedAgentTaxonomy | None:
        with strict_tenant_filter():
            async with get_async_db_session() as session:
                result = await session.execute(
                    select(ManagedAgentTaxonomy).where(
                        ManagedAgentTaxonomy.kind == kind,
                        ManagedAgentTaxonomy.code == code,
                    )
                )
                return result.scalars().first()

    @classmethod
    async def alist(
        cls,
        *,
        kind: str | None = None,
        enabled_only: bool = False,
    ) -> list[ManagedAgentTaxonomy]:
        conditions = []
        if kind:
            conditions.append(ManagedAgentTaxonomy.kind == kind)
        if enabled_only:
            conditions.append(ManagedAgentTaxonomy.is_enabled.is_(True))
        with strict_tenant_filter():
            async with get_async_db_session() as session:
                result = await session.execute(
                    select(ManagedAgentTaxonomy)
                    .where(*conditions)
                    .order_by(
                        ManagedAgentTaxonomy.kind.asc(),
                        ManagedAgentTaxonomy.sort_order.asc(),
                        ManagedAgentTaxonomy.id.asc(),
                    )
                )
                return list(result.scalars().all())

    @classmethod
    async def aupdate_fields(cls, row_id: int, fields: dict, *, tenant_id: int) -> int:
        if not fields:
            return 0
        fields = {**fields, "update_time": datetime.now()}
        async with get_async_db_session() as session:
            result = await session.execute(
                update(ManagedAgentTaxonomy)
                .where(
                    ManagedAgentTaxonomy.id == row_id,
                    ManagedAgentTaxonomy.tenant_id == tenant_id,
                )
                .values(**fields)
            )
            await session.commit()
            return result.rowcount or 0

    @classmethod
    async def adelete(cls, row_id: int, *, tenant_id: int) -> int:
        async with get_async_db_session() as session:
            result = await session.execute(
                delete(ManagedAgentTaxonomy).where(
                    ManagedAgentTaxonomy.id == row_id,
                    ManagedAgentTaxonomy.tenant_id == tenant_id,
                )
            )
            await session.commit()
            return result.rowcount or 0


class ManagedAgentRunDao:
    """DAO for ``managed_agent_run``: append + read-only stats."""

    @classmethod
    async def acreate(cls, row: ManagedAgentRun) -> ManagedAgentRun:
        async with get_async_db_session() as session:
            session.add(row)
            await session.commit()
            await session.refresh(row)
            return row

    @classmethod
    async def alist(
        cls,
        *,
        agent_id: int | None = None,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
        include_health_check: bool = False,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[list[ManagedAgentRun], int]:
        conditions = []
        if agent_id is not None:
            conditions.append(ManagedAgentRun.agent_id == agent_id)
        if start_time is not None:
            conditions.append(ManagedAgentRun.create_time >= start_time)
        if end_time is not None:
            conditions.append(ManagedAgentRun.create_time <= end_time)
        if not include_health_check:
            conditions.append(ManagedAgentRun.caller_type != "health_check")

        with strict_tenant_filter():
            async with get_async_db_session() as session:
                total = (await session.execute(select(func.count(ManagedAgentRun.id)).where(*conditions))).scalar() or 0
                result = await session.execute(
                    select(ManagedAgentRun)
                    .where(*conditions)
                    .order_by(ManagedAgentRun.create_time.desc())
                    .offset(max(page - 1, 0) * page_size)
                    .limit(page_size)
                )
                return list(result.scalars().all()), int(total)

    @classmethod
    async def astats(
        cls,
        *,
        agent_id: int | None = None,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
    ) -> dict:
        """调用量/成功数/失败数/成功率/平均延迟；默认排除 health_check。"""
        conditions = [ManagedAgentRun.caller_type != "health_check"]
        if agent_id is not None:
            conditions.append(ManagedAgentRun.agent_id == agent_id)
        if start_time is not None:
            conditions.append(ManagedAgentRun.create_time >= start_time)
        if end_time is not None:
            conditions.append(ManagedAgentRun.create_time <= end_time)

        success_case = case((ManagedAgentRun.status == ManagedRunStatus.SUCCESS.value, 1), else_=0)
        with strict_tenant_filter():
            async with get_async_db_session() as session:
                row = (
                    await session.execute(
                        select(
                            func.count(ManagedAgentRun.id),
                            func.coalesce(func.sum(success_case), 0),
                            func.avg(ManagedAgentRun.latency_ms),
                        ).where(*conditions)
                    )
                ).one()
        calls = int(row[0] or 0)
        success = int(row[1] or 0)
        failure = max(calls - success, 0)
        avg_latency = float(row[2]) if row[2] is not None else None
        return {
            "calls": calls,
            "success_count": success,
            "failure_count": failure,
            "success_rate": (success / calls) if calls else 0.0,
            "avg_latency_ms": avg_latency,
        }
