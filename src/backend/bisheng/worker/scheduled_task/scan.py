"""F077 调度轮次的 Celery 薄壳（beat 条目 ``scheduled_task_scan``，默认 20s）。

**薄壳只做三件事**：进 asyncio、拿到"现在"、把统计拍成可序列化的 dict。所有判定
与写回在 ``scheduled_task.domain.services.run_service.run_cycle`` 里，测试可以直接
调那个纯编排函数而不需要 broker/beat（design §7）。

``acks_late``：一轮扫描是幂等的（平台级 Redis 锁 + DB 原子 claim 双重去重），
worker 中途挂掉后由 broker 重投是安全的；``time_limit`` 只是纵深防御——真正的
"卡死判定"在任务租约上（design 决策 3/§5 坑 13）。
"""

from __future__ import annotations

from loguru import logger

from bisheng.scheduled_task.domain.services import run_service
from bisheng.worker._asyncio_utils import run_async_task
from bisheng.worker.main import bisheng_celery


@bisheng_celery.task(
    acks_late=True,
    time_limit=300,
    soft_time_limit=270,
    name="bisheng.worker.scheduled_task.scan.scan_scheduled_tasks",
)
def scan_scheduled_tasks() -> dict:
    """跑一轮：回收过期租约 → 枚举到期 → 判定/写回/派发 → 运行监督。

    返回值是 ``CycleStats.as_dict()``（``schedule.scan.round`` 的同一份计数），
    便于运维用 ``celery call`` 手工触发时直接看到本轮做了什么。
    """
    try:
        return run_async_task(run_service.run_cycle)
    except Exception:
        # 一轮失败不应把 beat 条目打成"持续失败"：下一 tick 会重新判定所有到期任务。
        # 计数与异常留在日志里，供 `schedule.scan.round` 的排障口径使用。
        logger.exception("scheduled_task: scan round crashed")
        return {"crashed": True}
