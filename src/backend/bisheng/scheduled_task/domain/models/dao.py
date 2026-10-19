"""F077 数据访问对象：``scheduled_task``。

**两种读取姿态，务必区分**（沿用 F076 DAO 的口径）：

- **调度路径**（``alist_due`` / ``acount_running`` / ``areclaim_expired``）：beat tick 的扫描
  是**跨租户**的，必须由调用方包 ``bypass_tenant_filter()``；命中后逐条
  ``set_current_tenant_id`` 再派发（design 决策 2 / §5 坑 10）。
- **管理面路径**（列表 / 编辑 / 删除）：任务是租户级配置，跨租户不可见
  （spec AC-53）。读取走租户自动过滤。

**所有原子写都判 ``rowcount``**（CAS 语义），且 bulk UPDATE 不吃租户自动过滤——
``tenant_id`` 条件由本 DAO 手写（C3 的已知边界，``src/backend/AGENTS.md`` §Known Pitfalls）。
**禁用 ``RETURNING``**：DM8 多行 RETURNING 以 ``[CODE:-5016]`` 拒绝（design §5 坑 17）。
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import delete, func, select, update

from bisheng.core.database import get_async_db_session
from bisheng.scheduled_task.domain.models.scheduled_task import (
    BlockedReason,
    RunStatus,
    ScheduledTask,
    TaskStatus,
)


class ScheduledTaskDao:
    """DAO for `scheduled_task`; all methods are classmethods (house convention)."""

    # ── 基础 CRUD（管理面） ──────────────────────────────────────────────

    @classmethod
    async def acreate(cls, task: ScheduledTask) -> ScheduledTask:
        async with get_async_db_session() as session:
            session.add(task)
            await session.commit()
            await session.refresh(task)
            return task

    @classmethod
    async def aget(cls, task_id: int) -> ScheduledTask | None:
        """管理面读取：租户自动过滤生效（跨租户读不到 → None）。"""
        async with get_async_db_session() as session:
            result = await session.execute(select(ScheduledTask).where(ScheduledTask.id == task_id))
            return result.scalars().first()

    @classmethod
    async def alist(
        cls,
        *,
        app_id: str | None = None,
        app_type: int | None = None,
        status: str | None = None,
        keyword: str | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[list[ScheduledTask], int]:
        """管理面列表（按应用过滤 + 名称模糊 + 分页）；租户自动过滤生效。"""
        conditions = []
        if app_id is not None:
            conditions.append(ScheduledTask.app_id == app_id)
        if app_type is not None:
            conditions.append(ScheduledTask.app_type == app_type)
        if status is not None:
            conditions.append(ScheduledTask.status == status)
        if keyword:
            conditions.append(ScheduledTask.name.like(f"%{keyword}%"))

        async with get_async_db_session() as session:
            total = (await session.execute(select(func.count(ScheduledTask.id)).where(*conditions))).scalar() or 0
            result = await session.execute(
                select(ScheduledTask)
                .where(*conditions)
                .order_by(ScheduledTask.create_time.desc())
                .offset(max(page - 1, 0) * page_size)
                .limit(page_size)
            )
            return list(result.scalars().all()), int(total)

    @classmethod
    async def aupdate_fields(cls, task_id: int, fields: dict, *, tenant_id: int) -> int:
        """管理面局部更新；手写租户条件（bulk UPDATE 不吃自动过滤）。返回 rowcount。"""
        if not fields:
            return 0
        fields = {**fields, "update_time": datetime.now()}
        async with get_async_db_session() as session:
            result = await session.execute(
                update(ScheduledTask)
                .where(ScheduledTask.id == task_id, ScheduledTask.tenant_id == tenant_id)
                .values(**fields)
            )
            await session.commit()
            return result.rowcount or 0

    @classmethod
    async def adelete(cls, task_id: int, *, tenant_id: int) -> int:
        async with get_async_db_session() as session:
            result = await session.execute(
                delete(ScheduledTask).where(ScheduledTask.id == task_id, ScheduledTask.tenant_id == tenant_id)
            )
            await session.commit()
            return result.rowcount or 0

    @classmethod
    async def acount_enabled_by_app(cls, app_id: str, app_type: int) -> int:
        """某应用下启用中的任务数：AC-05 上限校验 / AC-30 删除拦截。租户自动过滤生效。"""
        async with get_async_db_session() as session:
            return int(
                (
                    await session.execute(
                        select(func.count(ScheduledTask.id)).where(
                            ScheduledTask.app_id == app_id,
                            ScheduledTask.app_type == app_type,
                            ScheduledTask.status == TaskStatus.ENABLED.value,
                        )
                    )
                ).scalar()
                or 0
            )

    # ── 调度路径（跨租户；调用方负责 bypass_tenant_filter） ────────────────

    @classmethod
    async def alist_due(cls, now: datetime, limit: int = 200) -> list[ScheduledTask]:
        """到期任务（扫描唯一依据 = ``next_run_at``）。

        命中索引 ``(status, next_run_at)``。**调用方必须包 ``bypass_tenant_filter()``**
        ——beat tick 没有租户 header。
        """
        async with get_async_db_session() as session:
            result = await session.execute(
                select(ScheduledTask)
                .where(
                    ScheduledTask.status == TaskStatus.ENABLED.value,
                    ScheduledTask.next_run_at.is_not(None),
                    ScheduledTask.next_run_at <= now,
                )
                .order_by(ScheduledTask.next_run_at.asc())
                .limit(limit)
            )
            return list(result.scalars().all())

    @classmethod
    async def acount_running(cls, now: datetime) -> int:
        """全平台"运行中"任务数（租约未过期）——AC-12 的全局并发闸计数。

        过期租约不计入，计数自愈（不需要对账作业）。调用方包 ``bypass_tenant_filter()``。
        """
        async with get_async_db_session() as session:
            return int(
                (
                    await session.execute(
                        select(func.count(ScheduledTask.id)).where(
                            ScheduledTask.lease_expires_at.is_not(None),
                            ScheduledTask.lease_expires_at > now,
                        )
                    )
                ).scalar()
                or 0
            )

    @classmethod
    async def alist_with_live_lease(cls, now: datetime, limit: int = 200) -> list[ScheduledTask]:
        """持有未过期租约的任务——``observe_runs`` 的观察集合（design 决策 6）。"""
        async with get_async_db_session() as session:
            result = await session.execute(
                select(ScheduledTask)
                .where(
                    ScheduledTask.lease_expires_at.is_not(None),
                    ScheduledTask.lease_expires_at > now,
                )
                .limit(limit)
            )
            return list(result.scalars().all())

    @classmethod
    async def areclaim_expired(cls, now: datetime, *, limit: int = 200) -> list[dict]:
        """租约过期回收（AC-15）：先查过期行，再逐行 CAS 判失败并释放租约。

        返回成功回收的行（``[{id, tenant_id, name, created_by, trace_id}]``），
        供调用方计连续失败并按 AC-54 通知。**不依赖发起运行的进程存活**——
        回收者只是"下一个 tick"。
        """
        async with get_async_db_session() as session:
            result = await session.execute(
                select(ScheduledTask)
                .where(
                    ScheduledTask.lease_expires_at.is_not(None),
                    ScheduledTask.lease_expires_at <= now,
                )
                .limit(limit)
            )
            expired = list(result.scalars().all())

        reclaimed: list[dict] = []
        for row in expired:
            async with get_async_db_session() as session:
                result = await session.execute(
                    update(ScheduledTask)
                    .where(
                        ScheduledTask.id == row.id,
                        ScheduledTask.lease_expires_at.is_not(None),
                        ScheduledTask.lease_expires_at <= now,
                    )
                    .values(
                        run_lease_token=None,
                        run_started_at=None,
                        lease_expires_at=None,
                        last_status=RunStatus.FAILED.value,
                        last_error="run lease expired (timeout)",
                        last_finish_at=now,
                        consecutive_fail_count=ScheduledTask.consecutive_fail_count + 1,
                        update_time=now,
                    )
                )
                await session.commit()
                if (result.rowcount or 0) == 1:
                    reclaimed.append(
                        {
                            "id": row.id,
                            "tenant_id": row.tenant_id,
                            "name": row.name,
                            "created_by": row.created_by,
                            "trace_id": row.last_trace_id,
                            # 失败前计数：调用方据此判定"首败一条"与阈值停用
                            "fail_count_before": int(row.consecutive_fail_count or 0),
                            "fail_count_after": int(row.consecutive_fail_count or 0) + 1,
                        }
                    )
        return reclaimed

    # ── 原子写原语（CAS：rowcount 判定；调用方按 design 决策 4 的变体使用） ──

    @classmethod
    async def aclaim_lease(
        cls,
        task_id: int,
        *,
        token: str,
        now: datetime,
        lease_expires_at: datetime,
        next_run_at: datetime | None,
        planned_at: datetime | None,
        trace_id: str,
        keep_next_run_at: bool = False,
        require_due: bool = True,
    ) -> int:
        """**NORMAL 变体**：占租约 + 消耗本槽 + 写回下次（单条原子 UPDATE）。

        条件 = 启用中 + （``require_due`` 时）已到期 + （无租约或租约已过期）。
        ``keep_next_run_at=True`` 用于一次性任务（不推进 ``next_run_at``，终态时才
        自动停用）与任何非计划轮次触发（manual / event：不改变既定计划，也不写
        ``last_planned_at``）。

        ``require_due=False`` 给**立即运行与事件触发**用：用户点"立即运行"时任务
        通常还没到点，而计划轮次必须按到期才可抢——两者共用同一条原子写，只有
        "到期"这一项条件不同（W2 复核发现：直接复用计划 claim 会让未到期任务的
        rowcount=0，被调用方误判成"互斥中"而错回 11909）。
        """
        values: dict = {
            "run_lease_token": token,
            "run_started_at": now,
            "lease_expires_at": lease_expires_at,
            "last_trace_id": trace_id,
            "blocked_reason": None,
            "update_time": now,
        }
        if not keep_next_run_at:
            values["next_run_at"] = next_run_at
            values["last_planned_at"] = planned_at

        conditions = [
            ScheduledTask.id == task_id,
            ScheduledTask.status == TaskStatus.ENABLED.value,
            (ScheduledTask.lease_expires_at.is_(None)) | (ScheduledTask.lease_expires_at <= now),
        ]
        if require_due:
            conditions.append(ScheduledTask.next_run_at.is_not(None))
            conditions.append(ScheduledTask.next_run_at <= now)

        async with get_async_db_session() as session:
            result = await session.execute(update(ScheduledTask).where(*conditions).values(**values))
            await session.commit()
            return result.rowcount or 0

    @classmethod
    async def amark_skipped(
        cls, task_id: int, *, slot: datetime | None, next_run_at: datetime | None, reason: str, now: datetime
    ) -> int:
        """**SKIP 变体**（周期任务）：不占租约，消耗本槽并记录跳过原因。

        条件含 ``next_run_at == slot``（槽位校验）：与"已被别的轮次处理"互斥。
        """
        async with get_async_db_session() as session:
            result = await session.execute(
                update(ScheduledTask)
                .where(
                    ScheduledTask.id == task_id,
                    ScheduledTask.status == TaskStatus.ENABLED.value,
                    ScheduledTask.next_run_at == slot,
                )
                .values(
                    next_run_at=next_run_at,
                    last_planned_at=slot,
                    last_status=RunStatus.SKIPPED.value,
                    last_error=reason,
                    last_finish_at=now,
                    update_time=now,
                )
            )
            await session.commit()
            return result.rowcount or 0

    @classmethod
    async def amark_failed(
        cls,
        task_id: int,
        *,
        slot: datetime | None,
        next_run_at: datetime | None,
        reason: str,
        now: datetime,
        disable: bool = False,
    ) -> int:
        """**FAIL 变体**：不占租约，记账失败并推进连续失败计数（AC-22 / AC-31）。

        ``disable=True`` 用于 AC-31（目标应用已删除 → fail-closed 并自动停用）。
        """
        values: dict = {
            "next_run_at": next_run_at,
            "last_planned_at": slot,
            "last_status": RunStatus.FAILED.value,
            "last_error": reason,
            "last_finish_at": now,
            "consecutive_fail_count": ScheduledTask.consecutive_fail_count + 1,
            "blocked_reason": None,
            "update_time": now,
        }
        if disable:
            values["status"] = TaskStatus.DISABLED.value
        async with get_async_db_session() as session:
            result = await session.execute(
                update(ScheduledTask)
                .where(
                    ScheduledTask.id == task_id,
                    ScheduledTask.status == TaskStatus.ENABLED.value,
                    ScheduledTask.next_run_at == slot,
                )
                .values(**values)
            )
            await session.commit()
            return result.rowcount or 0

    @classmethod
    async def aset_blocked(cls, task_id: int, reason: BlockedReason, *, now: datetime) -> int:
        """**DEFER 变体的标记**（一次性任务等待应用上线）：幂等置位。

        rowcount=1 表示本次才置位（可选择据此通知一次）；不改 ``next_run_at``、
        不占租约、不动 ``last_status``——"只有一次机会"不因等待被消耗（spec AC-16）。
        """
        async with get_async_db_session() as session:
            result = await session.execute(
                update(ScheduledTask)
                .where(
                    ScheduledTask.id == task_id,
                    ScheduledTask.blocked_reason.is_(None),
                )
                .values(blocked_reason=reason.value, update_time=now)
            )
            await session.commit()
            return result.rowcount or 0

    @classmethod
    async def afinalize(
        cls,
        task_id: int,
        *,
        token: str | None,
        status: RunStatus,
        error: str | None,
        now: datetime,
        duration_ms: int | None = None,
        app_version_id: str | None = None,
        trace_id: str | None = None,
        disable: bool = False,
        reset_fail_count: bool = False,
        increment_fail_count: bool = False,
    ) -> int:
        """**终态回写**：释放租约 + 写最近结果（AC-14 / AC-08 的自动停用）。

        条件含 ``run_lease_token == token``（本人租约才可释放，防误释放他人）；
        ``token=None`` 表示不校验令牌（回收路径已自带 CAS 条件，不走这里）。
        """
        values: dict = {
            "run_lease_token": None,
            "run_started_at": None,
            "lease_expires_at": None,
            "last_status": status.value,
            "last_error": error,
            "last_finish_at": now,
            "blocked_reason": None,
            "update_time": now,
        }
        if duration_ms is not None:
            values["last_duration_ms"] = duration_ms
        if app_version_id is not None:
            values["last_app_version_id"] = app_version_id
        if trace_id is not None:
            values["last_trace_id"] = trace_id
        if disable:
            values["status"] = TaskStatus.DISABLED.value
        if reset_fail_count:
            values["consecutive_fail_count"] = 0
        elif increment_fail_count:
            values["consecutive_fail_count"] = ScheduledTask.consecutive_fail_count + 1

        conditions = [ScheduledTask.id == task_id]
        if token is not None:
            conditions.append(ScheduledTask.run_lease_token == token)
        async with get_async_db_session() as session:
            result = await session.execute(update(ScheduledTask).where(*conditions).values(**values))
            await session.commit()
            return result.rowcount or 0

    @classmethod
    async def amark_needs_input(cls, task_id: int, *, token: str, error: str | None, now: datetime) -> int:
        """INPUT 无预设输入的暂停态：**CAS 兼作"已通知"哨兵**（design 决策 6）。

        只在状态从"非 needs_input"转过来时返回 1——调用方据此决定是否发通知，
        不新增通知标志位。
        """
        async with get_async_db_session() as session:
            result = await session.execute(
                update(ScheduledTask)
                .where(
                    ScheduledTask.id == task_id,
                    ScheduledTask.run_lease_token == token,
                    ScheduledTask.last_status.is_not(RunStatus.NEEDS_INPUT.value),
                )
                .values(last_status=RunStatus.NEEDS_INPUT.value, last_error=error, update_time=now)
            )
            await session.commit()
            return result.rowcount or 0
