"""F078 纳管智能体调用留痕（``managed_agent_run``）。

每条记录 = 一次统一调用 / 定时运行 / 健康试调用。它是运营统计与排障的**权威
留痕**；``managed_agent.invoke`` 审计只是 best-effort 的旁路（design 决策 14）。
表无 FK/级联：agent 硬删除后记录保留，因此三列 ``*_snapshot`` 必须写入。

只存标识与结果摘要：不存输入正文、不存凭据、不存上游正文（INV-11 / INV-26）。
``usage`` 用 ``JsonType``（整读整写，不查询）。回滚：

    DROP TABLE IF EXISTS managed_agent_run;
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from sqlalchemy import BigInteger, Column, DateTime, Index, Integer, String, UniqueConstraint, text
from sqlmodel import Field

from bisheng.common.models.base import SQLModelSerializable
from bisheng.core.database.dialect_helpers import JsonType


class CallerType(StrEnum):
    """调用来源（列 ``caller_type``，String(16)）。"""

    API_KEY = "api_key"
    SCHEDULE = "schedule"
    MANUAL = "manual"
    HEALTH_CHECK = "health_check"


class ManagedRunStatus(StrEnum):
    """单次运行结果（列 ``status``，String(16)）。"""

    SUCCESS = "success"
    FAILED = "failed"


class ManagedAgentRun(SQLModelSerializable, table=True):
    """One invocation/run trace row for a managed agent (AC-27/AC-43)."""

    __tablename__ = "managed_agent_run"
    __table_args__ = (
        UniqueConstraint("tenant_id", "run_id", name="uk_managed_agent_run_tenant_run_id"),
        Index("idx_managed_agent_run_tenant_agent_time", "tenant_id", "agent_id", "create_time"),
        Index("idx_managed_agent_run_tenant_time", "tenant_id", "create_time"),
        Index("idx_managed_agent_run_tenant_status", "tenant_id", "status", "create_time"),
        {"mysql_charset": "utf8mb4", "mysql_collate": "utf8mb4_unicode_ci"},
    )

    id: int | None = Field(
        default=None,
        sa_column=Column(
            # MySQL/DM8 保持 BIGINT；SQLite 只有 INTEGER 主键才会走 rowid 自增，
            # 测试用内存 SQLite，故用 variant 降级（生产语义不变）。
            BigInteger().with_variant(Integer, "sqlite"),
            primary_key=True,
            autoincrement=True,
            comment="主键",
        ),
    )
    tenant_id: int = Field(
        default=None,
        sa_column=Column(Integer, nullable=False, index=True, comment="租户ID"),
    )
    agent_id: int = Field(
        sa_column=Column(Integer, nullable=False, comment="纳管智能体ID(无FK,删除后保留)"),
    )
    agent_name_snapshot: str | None = Field(
        default=None,
        sa_column=Column(String(128), nullable=True, comment="调用时的智能体名称快照"),
    )
    source_kind_snapshot: str | None = Field(
        default=None,
        sa_column=Column(String(16), nullable=True, comment="调用时的来源快照"),
    )
    protocol_snapshot: str | None = Field(
        default=None,
        sa_column=Column(String(32), nullable=True, comment="调用时的协议快照"),
    )
    run_id: str = Field(
        sa_column=Column(String(64), nullable=False, comment="平台运行标识(UUID)"),
    )
    trace_id: str | None = Field(
        default=None,
        sa_column=Column(String(64), nullable=True, comment="工作流/助手运行 trace 标识"),
    )
    caller_type: str = Field(
        sa_column=Column(String(16), nullable=False, comment="调用来源(api_key/schedule/manual/health_check)"),
    )
    caller_id: str | None = Field(
        default=None,
        sa_column=Column(String(64), nullable=True, comment="调用方标识(服务账号/用户/任务)"),
    )
    service_account_id: int | None = Field(
        default=None,
        sa_column=Column(Integer, nullable=True, comment="服务账号ID"),
    )
    scheduled_task_id: int | None = Field(
        default=None,
        sa_column=Column(Integer, nullable=True, comment="定时任务ID"),
    )
    trigger_kind: str | None = Field(
        default=None,
        sa_column=Column(String(16), nullable=True, comment="触发方式(cron/once/event/manual)"),
    )
    status: str = Field(
        sa_column=Column(String(16), nullable=False, comment="结果(success/failed)"),
    )
    error_code: int | None = Field(
        default=None,
        sa_column=Column(Integer, nullable=True, comment="业务错误码"),
    )
    error_summary: str | None = Field(
        default=None,
        sa_column=Column(String(512), nullable=True, comment="脱敏后的失败摘要(不含上游正文)"),
    )
    latency_ms: int | None = Field(
        default=None,
        sa_column=Column(Integer, nullable=True, comment="耗时(ms)"),
    )
    upstream_status: int | None = Field(
        default=None,
        sa_column=Column(Integer, nullable=True, comment="上游HTTP状态码(仅摘要)"),
    )
    usage: dict | None = Field(
        default=None,
        sa_column=Column(JsonType, nullable=True, comment="可选用量信息(整读整写)"),
    )
    create_time: datetime | None = Field(
        default=None,
        sa_column=Column(
            DateTime,
            nullable=False,
            server_default=text("CURRENT_TIMESTAMP"),
            comment="创建时间",
        ),
    )
