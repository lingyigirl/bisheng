"""F077 定时任务表（``scheduled_task``）。

一条任务 = 一个应用（工作流或助手）+ 一个触发计划（周期 cron 或一次性绝对时间）
+ 一个运行身份（F076 服务账号专用身份，创建时选定、运行期锁定，INV-25）
+ 可选的预设输入。运行态（互斥 / 租约 / 最近结果）也收在同一行——「同一任务至多
一个运行」使任务行本身就是运行时状态的最佳载体（design §4.2）。

租户感知：本表登记在 ``core/database/tenant_filter.py`` 的
``_TENANT_AWARE_MODEL_MODULES``；``tenant_id`` 必须 ``default=None``
（``test/tenant/test_tenant_id_default_guard.py`` 静态守卫）。

双库约束（C2）：``preset_inputs`` 用 ``dialect_helpers.JsonType``（DM 上退化为
CLOB，只整读整写、**永不作为查询条件**）；``update_time`` 用
``UPDATE_TIME_SERVER_DEFAULT``；所有原子 UPDATE 判 ``rowcount``，**禁用
``RETURNING``**（DM8 多行 RETURNING 以 ``[CODE:-5016]`` 拒绝）。

回滚（本特性不写 Alembic revision，整表由 SQLModel ``create_all`` 建）：

    DROP TABLE IF EXISTS scheduled_task;

并同步从 ``_TENANT_AWARE_MODEL_MODULES`` 摘除本模型路径。
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from sqlalchemy import Column, DateTime, Index, Integer, String, text
from sqlmodel import Field

from bisheng.common.models.base import SQLModelSerializable
from bisheng.core.database.dialect_helpers import UPDATE_TIME_SERVER_DEFAULT, JsonType


class ScheduleType(StrEnum):
    """触发计划形态（列 ``schedule_type``，String(16)）。"""

    CRON = "cron"
    ONCE = "once"


class TaskStatus(StrEnum):
    """任务启停状态（列 ``status``，String(16)）；一次性任务执行后自动转 ``DISABLED``。"""

    ENABLED = "enabled"
    DISABLED = "disabled"


class RunStatus(StrEnum):
    """最近一轮运行的结果（列 ``last_status``，String(16)）。

    ``NEEDS_INPUT`` 是**暂停状态**而非终态：它同时兼作「已通知创建者」的
    去重哨兵（CAS 写入，rowcount=1 才发通知）；该轮最迟在运行时长上限被
    回收为 ``FAILED``（spec AC-46）。
    """

    SUCCESS = "success"
    FAILED = "failed"
    SKIPPED = "skipped"
    NEEDS_INPUT = "needs_input"


class BlockedReason(StrEnum):
    """非消费性阻塞原因（列 ``blocked_reason``）。

    一次性任务在等待目标应用上线期间置位——**不写进 ``last_status``**
    （它没有产生过一轮运行，写进去会污染「上次结果」列，spec AC-58）。
    """

    APP_OFFLINE = "app_offline"


class ScheduledTask(SQLModelSerializable, table=True):
    """A scheduled run of one application (workflow or assistant).

    The run identity is locked at creation (INV-25): every run executes as
    ``run_identity_user_id`` and no trigger path may override it. ``created_by``
    is the human owner used for audit and failure notifications — the two are
    deliberately separate columns.
    """

    __tablename__ = "scheduled_task"
    __table_args__ = (
        # 跨租户到期扫描是最热查询：单条 `WHERE status='enabled' AND next_run_at <= now`
        # （bypass_tenant_filter），故索引不带 tenant_id 前缀。
        Index("idx_scheduled_task_due", "status", "next_run_at"),
        # 管理面列表 / 每应用任务数上限（AC-05）/ 删除拦截计数（AC-30）。
        Index("idx_scheduled_task_tenant_app", "tenant_id", "app_id", "app_type"),
        # 监督过（observe_runs）的租约过期扫描。
        Index("idx_scheduled_task_lease", "lease_expires_at"),
        {"mysql_charset": "utf8mb4", "mysql_collate": "utf8mb4_unicode_ci"},
    )

    id: int | None = Field(default=None, primary_key=True)
    tenant_id: int = Field(
        default=None,
        sa_column=Column(Integer, nullable=False, index=True, comment="租户ID"),
    )
    name: str = Field(
        sa_column=Column(String(128), nullable=False, comment="任务名称"),
    )
    description: str | None = Field(
        default=None,
        sa_column=Column(String(512), nullable=True, comment="描述"),
    )
    app_id: str = Field(
        sa_column=Column(String(64), nullable=False, comment="目标应用ID(工作流或助手)"),
    )
    app_type: int = Field(
        sa_column=Column(Integer, nullable=False, comment="应用类型(FlowType: assistant=5/workflow=10)"),
    )
    schedule_type: str = Field(
        default=ScheduleType.CRON.value,
        sa_column=Column(String(16), nullable=False, server_default=text("'cron'"), comment="触发计划形态(cron/once)"),
    )
    cron_expression: str | None = Field(
        default=None,
        sa_column=Column(String(64), nullable=True, comment="归一化后的5段cron表达式(仅周期任务)"),
    )
    run_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime, nullable=True, comment="一次性任务的绝对时间(服务器时区);仅用于展示与等待上线语义"),
    )
    service_account_id: int = Field(
        sa_column=Column(Integer, nullable=False, comment="运行身份所属服务账号(F076),触发期断言resource_owner一致"),
    )
    run_identity_user_id: int = Field(
        sa_column=Column(Integer, nullable=False, comment="运行身份用户ID(F076专用身份);运行期锁定"),
    )
    preset_inputs: list | None = Field(
        default=None,
        sa_column=Column(JsonType, nullable=True, comment="预设输入[{key,label,value}];按静态label存放;永不作查询条件"),
    )
    status: str = Field(
        default=TaskStatus.ENABLED.value,
        sa_column=Column(
            String(16), nullable=False, server_default=text("'enabled'"), comment="启停状态(enabled/disabled)"
        ),
    )
    next_run_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime, nullable=True, comment="扫描唯一依据;once初始=run_at;每轮claim/skip/fail后推进"),
    )
    last_planned_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime, nullable=True, comment="本轮本应触发的计划槽位(AC-10偏差对账用)"),
    )
    last_run_at: datetime | None = Field(
        default=None, sa_column=Column(DateTime, nullable=True, comment="本次运行开始时间")
    )
    last_finish_at: datetime | None = Field(
        default=None, sa_column=Column(DateTime, nullable=True, comment="本次运行结束时间")
    )
    last_status: str | None = Field(
        default=None,
        sa_column=Column(
            String(16), nullable=True, comment="最近结果(success/failed/skipped/needs_input);needs_input兼作已通知哨兵"
        ),
    )
    last_duration_ms: int | None = Field(
        default=None, sa_column=Column(Integer, nullable=True, comment="本次运行耗时(毫秒)")
    )
    last_error: str | None = Field(
        default=None,
        sa_column=Column(String(512), nullable=True, comment="失败/跳过原因摘要(如overlap/app_offline);不含正文"),
    )
    last_trace_id: str | None = Field(
        default=None,
        sa_column=Column(String(64), nullable=True, comment="本次运行的unique_id(=监控trace_id),用于一跳归因"),
    )
    last_app_version_id: str | None = Field(
        default=None,
        sa_column=Column(String(64), nullable=True, comment="本次所用版本标识(工作流FlowVersion.id;助手NULL)"),
    )
    blocked_reason: str | None = Field(
        default=None,
        sa_column=Column(String(32), nullable=True, comment="非消费性阻塞原因(如app_offline);不进last_status"),
    )
    run_started_at: datetime | None = Field(
        default=None, sa_column=Column(DateTime, nullable=True, comment="运行中标记的开始时间")
    )
    run_lease_token: str | None = Field(
        default=None,
        sa_column=Column(String(64), nullable=True, comment="claim令牌;释放/续期核对,防误释放他人租约"),
    )
    lease_expires_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime, nullable=True, comment="租约deadline;过期即视同空闲并可被回收判失败"),
    )
    consecutive_fail_count: int = Field(
        default=0,
        sa_column=Column(
            Integer, nullable=False, server_default=text("0"), comment="连续失败计数(仅failed计入;success清零)"
        ),
    )
    created_by: int = Field(
        sa_column=Column(Integer, nullable=False, comment="创建者ID(通知收件人,创建时锁定)"),
    )
    create_time: datetime = Field(
        default_factory=datetime.now,
        sa_column=Column(DateTime, nullable=False, server_default=text("CURRENT_TIMESTAMP"), comment="创建时间"),
    )
    update_time: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime, nullable=True, server_default=UPDATE_TIME_SERVER_DEFAULT, comment="更新时间"),
    )
