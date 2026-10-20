"""F077 助手应用的无人值守运行器（design 决策 5）。

工作流走既有的 ``execute_workflow``（有 Redis 控制面与 F046/F052 遥测）；
助手没有那套链路，所以本模块**镜像 v2 无状态调用的核心**：

    AssistantAgent(assistant_info, chat_id="", invoke_user_id=运行身份)
        → init_assistant() → run(question)

三条边界（都不是可选项）：
1. **不持密钥、不走 HTTP** —— F076 的密钥不可回读（INV-23①），进程内直调服务层；
2. **不落会话/消息**（``chat_id=""``）——定时运行没有会话落点（AC-48）；
3. **补一条 APPLICATION_PROCESS 遥测** —— 工作流侧由共享执行链自动上报，助手侧
   原本只有 v2 端点自己写（``open_endpoints/api/endpoints/assistant.py:274-305``），
   不补则 AC-52 的"一跳进监控"对助手任务无数据（已在 release-contract 登记）。

失败一律走 ``run_service._finalize``：释放租约、写最近结果、计连续失败、发首败
通知、写 ``schedule.run`` 留痕——与计划轮次的终态回写完全同源（单一写入者）。
"""

from __future__ import annotations

import time

from loguru import logger

from bisheng.common.constants.enums.telemetry import ApplicationTypeEnum, BaseTelemetryTypeEnum, StatusEnum
from bisheng.common.schemas.telemetry.event_data_schema import ApplicationProcessEventData
from bisheng.common.services import telemetry_service
from bisheng.database.models.assistant import AssistantDao
from bisheng.scheduled_task.domain.models.dao import ScheduledTaskDao
from bisheng.scheduled_task.domain.models.scheduled_task import RunStatus
from bisheng.scheduled_task.domain.services import run_service, trigger_guard
from bisheng.worker._asyncio_utils import run_async_task
from bisheng.worker.main import bisheng_celery


@bisheng_celery.task(
    acks_late=True,
    time_limit=3600,
    soft_time_limit=3540,
    name="bisheng.worker.scheduled_task.assistant_runner.execute_assistant_run",
)
def execute_assistant_run(task_id: int, trace_id: str) -> bool:
    """跑一轮助手应用。``trace_id`` 即本轮租约令牌（也是监控 trace_id）。"""
    return run_async_task(lambda: _execute_assistant_run_async(task_id, trace_id))


def _preset_question(task) -> str | None:
    """助手任务的"问题"来自预设输入：优先 key/label 为 question，否则取第一项。"""
    presets = task.preset_inputs or []
    for preset in presets:
        label = (preset.get("key") or preset.get("label") or "").strip().lower()
        if label in ("question", "问题", "提问"):
            return str(preset.get("value") or "").strip() or None
    if presets:
        return str(presets[0].get("value") or "").strip() or None
    return None


async def _execute_assistant_run_async(task_id: int, trace_id: str) -> bool:
    task = await ScheduledTaskDao.aget(task_id)
    if task is None:
        # 任务在派发后被删（或租户上下文不对）：没有可回写的目标，直接结束。
        logger.warning("scheduled_task: assistant run for deleted task {}", task_id)
        return False

    started_at = time.time()
    status = RunStatus.FAILED
    error: str | None = None
    # AC-52：监控「智能体」列应显示应用名，而不是定时任务名；应用查不到/已删除时
    # 回退任务名，保证遥测仍可归因。
    app_name = task.name
    try:
        # 执行级二次断言（自有任务体，零额外成本）：队列等待期间应用可能下线/身份被撤。
        recheck = await trigger_guard.recheck_task(task)
        if not recheck.ok:
            error = trigger_guard.describe_failure(recheck)
            return False

        question = _preset_question(task)
        if question is None:
            error = "no_preset_question"
            return False

        assistant = await AssistantDao.aget_one_assistant(str(task.app_id))
        if assistant is None:
            error = "app_deleted"
            return False
        app_name = assistant.name

        # 进程内直调（镜像 v2 无状态核心）；chat_id="" 即不落会话。
        from bisheng.api.services.assistant_agent import AssistantAgent

        agent = AssistantAgent(
            assistant_info=assistant,
            chat_id="",
            invoke_user_id=task.run_identity_user_id,
        )
        await agent.init_assistant()
        await agent.run(question)
        status = RunStatus.SUCCESS
        return True
    except Exception as exc:
        error = str(exc)[:480] or exc.__class__.__name__
        logger.exception("scheduled_task: assistant run failed for task {}", task_id)
        return False
    finally:
        end_time = time.time()
        await _report_telemetry(task, started_at, end_time, status, app_name=app_name, trace_id=trace_id)
        await run_service._finalize(
            task,
            token=trace_id,
            status=status,
            error=error,
            now=run_service._now(),
            notify=run_service._notify,
            count_failure=(status is RunStatus.FAILED),
        )


async def _report_telemetry(
    task,
    started_at: float,
    end_time: float,
    status: RunStatus,
    *,
    app_name: str,
    trace_id: str,
) -> None:
    """APPLICATION_PROCESS 上报（复用既有 schema，不新增字段）。

    best-effort：遥测写失败不得改变运行结果（与工作流侧 finally 上报同口径）。
    ``app_name`` 必须是应用名（AC-52）；``trace_id`` 用本轮租约令牌，避免读
    任务行上的旧值。
    """
    try:
        await telemetry_service.log_event(
            user_id=task.run_identity_user_id,
            event_type=BaseTelemetryTypeEnum.APPLICATION_PROCESS,
            trace_id=trace_id,
            event_data=ApplicationProcessEventData(
                app_id=str(task.app_id),
                app_name=app_name,
                app_type=ApplicationTypeEnum.ASSISTANT,
                chat_id="",
                start_time=int(started_at),
                end_time=int(end_time),
                process_time=int((end_time - started_at) * 1000),
                status=StatusEnum.SUCCESS if status is RunStatus.SUCCESS else StatusEnum.FAILED,
            ),
        )
    except Exception:
        logger.warning("scheduled_task: telemetry write failed for task {}", task.id)
