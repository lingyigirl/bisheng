"""Database claims and transaction fencing for recovery-enabled tasks."""

import time
from uuid import uuid4

from sqlalchemy import event, select
from sqlalchemy.orm import Session

from bisheng.core.context.tenant import bypass_tenant_filter
from bisheng.core.database import get_async_db_session
from bisheng.linsight.domain.models.linsight_session_version import LinsightSessionVersion, SessionVersionStatusEnum
from bisheng.linsight.domain.services.recovery_context import RecoveryLeaseLost, execution_fence
from bisheng.linsight.domain.services.recovery_queue_scope import recovery_queue_namespace, scoped_recovery_state

TERMINAL = {
    SessionVersionStatusEnum.COMPLETED,
    SessionVersionStatusEnum.FAILED,
    SessionVersionStatusEnum.TERMINATED,
    SessionVersionStatusEnum.SOP_GENERATION_FAILED,
}


class RecoveryRepository:
    async def prepare_continue(self, task_id, question):
        async with get_async_db_session() as session:
            row = (
                await session.execute(
                    select(LinsightSessionVersion).where(LinsightSessionVersion.id == task_id).with_for_update()
                )
            ).scalar_one()
            if row.status not in {SessionVersionStatusEnum.COMPLETED, SessionVersionStatusEnum.FAILED}:
                return False
            # Persist the input before enqueueing: the recovery scanner can
            # deliver it even if the queue write or the worker is interrupted.
            row.recovery_state = scoped_recovery_state(
                {
                    **(row.recovery_state or {}),
                    "continuation": {"id": uuid4().hex, "question": question, "receipt_epoch": row.recovery_epoch + 1},
                    "phase": "continue_queued",
                    "attempts": 0,
                },
                time.time() + 5,
            )
            row.status = SessionVersionStatusEnum.IN_PROGRESS
            await session.commit()
            return True

    async def candidates(self, *, after_id=None, limit=256):
        # The scheduler has no user context; each candidate restores its own tenant.
        with bypass_tenant_filter():
            async with get_async_db_session() as session:
                statement = select(
                    LinsightSessionVersion.id,
                    LinsightSessionVersion.tenant_id,
                    LinsightSessionVersion.recovery_state,
                ).where(
                    LinsightSessionVersion.status == SessionVersionStatusEnum.IN_PROGRESS,
                    # Only protocol-enabled executions receive a fenced epoch.
                    LinsightSessionVersion.recovery_epoch > 0,
                )
                if after_id is not None:
                    statement = statement.where(LinsightSessionVersion.id > after_id)
                result = await session.execute(statement.order_by(LinsightSessionVersion.id).limit(limit))
                return result.all()

    async def claim(self, task_id, *, recovering=False):
        with bypass_tenant_filter():
            async with get_async_db_session() as session:
                row = (
                    await session.execute(
                        select(LinsightSessionVersion).where(LinsightSessionVersion.id == task_id).with_for_update()
                    )
                ).scalar_one_or_none()
                if row is None or row.status in TERMINAL:
                    return None
                if recovering and row.status != SessionVersionStatusEnum.IN_PROGRESS:
                    return None
                state = dict(row.recovery_state or {})
                if state.get("queue_namespace", recovery_queue_namespace()) != recovery_queue_namespace():
                    return None
                if (
                    not recovering
                    and row.status == SessionVersionStatusEnum.IN_PROGRESS
                    and state.get("phase") != "continue_queued"
                ):
                    return None
                attempts = int(state.get("attempts", 0)) + int(recovering)
                row.recovery_epoch += 1
                row.status = SessionVersionStatusEnum.IN_PROGRESS
                row.recovery_state = scoped_recovery_state(
                    {
                        **state,
                        "attempts": attempts,
                        "phase": "recovering" if recovering else "running",
                    },
                    time.time() + min(120, 5 * 2 ** min(attempts, 5)),
                )
                await session.commit()
                await session.refresh(row)
                return row

    async def workspace_ready(self, task_id, epoch):
        async with get_async_db_session() as session:
            row = (
                await session.execute(
                    select(LinsightSessionVersion).where(LinsightSessionVersion.id == task_id).with_for_update()
                )
            ).scalar_one()
            row.recovery_state = {**(row.recovery_state or {}), "workspace_epoch": epoch}
            await session.commit()
            return dict(row.recovery_state)

    async def save_output_baseline(self, task_id, baseline):
        async with get_async_db_session() as session:
            row = (
                await session.execute(
                    select(LinsightSessionVersion).where(LinsightSessionVersion.id == task_id).with_for_update()
                )
            ).scalar_one()
            row.recovery_state = {**(row.recovery_state or {}), "inherited_output_md5": baseline}
            await session.commit()
            return dict(row.recovery_state)

    async def terminate(self, task_id):
        async with get_async_db_session() as session:
            row = (
                await session.execute(
                    select(LinsightSessionVersion).where(LinsightSessionVersion.id == task_id).with_for_update()
                )
            ).scalar_one()
            if row.status in TERMINAL:
                return row
            row.status = SessionVersionStatusEnum.TERMINATED
            row.recovery_state = {**(row.recovery_state or {}), "phase": "terminated"}
            await session.commit()
            await session.refresh(row)
            return row

    @staticmethod
    def guard(session):
        fence = execution_fence.get()
        if fence is None:
            return
        fence.check_sync()
        marker = (fence.task_id, fence.epoch, session.get_transaction())
        if session.info.get("recovery_fence") == marker:
            return
        table = LinsightSessionVersion.__table__
        # Lock until transaction end, serializing a new epoch against old writers.
        row = (
            session.connection()
            .execute(
                select(table.c.recovery_epoch, table.c.status).where(table.c.id == fence.task_id).with_for_update()
            )
            .first()
        )
        if row is None or row.recovery_epoch != fence.epoch or row.status == SessionVersionStatusEnum.TERMINATED:
            raise RecoveryLeaseLost("Execution epoch superseded or task ended")
        session.info["recovery_fence"] = (fence.task_id, fence.epoch, session.get_transaction())


@event.listens_for(Session, "before_flush")
def _guard_flush(session, flush_context, instances):
    if session.new or session.dirty or session.deleted:
        RecoveryRepository.guard(session)


@event.listens_for(Session, "do_orm_execute")
def _guard_dml(state):
    if state.is_update or state.is_delete or state.is_insert:
        RecoveryRepository.guard(state.session)


@event.listens_for(Session, "before_commit")
def _guard_commit(session):
    fence = execution_fence.get()
    if fence and (session.info.get("recovery_fence") or session.new or session.dirty or session.deleted):
        fence.check_sync()


@event.listens_for(Session, "after_transaction_end")
def _clear_fence(session, transaction):
    if transaction.parent is None:
        session.info.pop("recovery_fence", None)
