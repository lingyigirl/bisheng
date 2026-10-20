"""F077 定时任务的领域常量。

审计动作名放这里（与 `TenantAuditAction` / `DocExtractAuditAction` /
`OpenApiAuditAction` 同约定）：裸字符串拼错会**行照样写入、但查询永远命中不了**
且不报错，所以集中成枚举。动作名由 design §6.1 固定，两棵树：

- ``schedule.task.*`` —— 六个管理动作，**进**审计页可见白名单
  （``_UI_VISIBLE_V2_ACTIONS``——改了任务，后续每次运行的行为都跟着变，
  必须可事后查阅；对齐 F058 doc_extract 的 INV-15 精神）。
- ``schedule.run`` —— 运行动作，只落库取证、**不进**白名单
  （对齐 F076 的 ``open_api.call``：运行频率由调度决定，审计页会被刷屏）。

命名空间 ``schedule`` 需在 ``database/models/audit_log.py`` 的
``_V2_NAMESPACE_TO_ACTION_PREFIX`` 与前端 ``log.ts`` 的镜像表**两处**登记。
"""

from __future__ import annotations

from enum import StrEnum


class ScheduledTaskAuditAction(StrEnum):
    """审计动作词表（design §6.1 的动作名清单）。"""

    #: 任务创建（含"自然语言草稿确认后落库"——落库这一刻就是创建）。
    TASK_CREATE = "schedule.task.create"
    #: 任务编辑（触发计划 / 预设输入 / 运行身份 / 名称描述的变更；含立即运行的前后值留痕）。
    TASK_UPDATE = "schedule.task.update"
    #: 启用（重新启用时 next_run_at 会被重算为下一个未来计划点）。
    TASK_ENABLE = "schedule.task.enable"
    #: 人工停用。一次性任务终态、达阈值、应用删除等**自动停用**不落本动作：
    #: 它们与运行取证一起落 ``schedule.run``（系统身份），保持不进审计页白名单。
    TASK_DISABLE = "schedule.task.disable"
    #: 删除任务（运行留痕与审计不随删除消失）。
    TASK_DELETE = "schedule.task.delete"
    #: 手动立即运行（人触发的动作，进审计页：谁在什么时候跳过计划跑了一次）。
    TASK_RUN_NOW = "schedule.task.run_now"
    #: 运行与系统处置留痕。正常运行的 operator = 运行身份（服务账号专用身份）；
    #: 系统自动处置（计划轮次失败、租约超时判失败、达阈值/应用删除/一次性终态
    #: 自动停用）以 ``operator_id=0`` → ``'system'`` 留痕。
    RUN = "schedule.run"


#: 进审计页可见白名单的管理动作（顺序即前端镜像表的顺序）。
UI_VISIBLE_ACTIONS: tuple[str, ...] = (
    ScheduledTaskAuditAction.TASK_CREATE.value,
    ScheduledTaskAuditAction.TASK_UPDATE.value,
    ScheduledTaskAuditAction.TASK_ENABLE.value,
    ScheduledTaskAuditAction.TASK_DISABLE.value,
    ScheduledTaskAuditAction.TASK_DELETE.value,
    ScheduledTaskAuditAction.TASK_RUN_NOW.value,
)
