"""F077 定时任务管理面的请求/响应模型。

字段口径对齐 design §4.2：前端只依赖这里的键，任务行的其余列（租约、计数、
阻塞原因）属实现细节，不对外。
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class PresetInputItem(BaseModel):
    """一条预设输入（静态 label/key → 值）。文件类在一期被拒绝（AC-47）。"""

    key: str = Field(..., description="输入项的静态 label/key")
    value: str = Field(default="", description="提交值")


class ScheduledTaskCreateReq(BaseModel):
    name: str = Field(..., max_length=128, description="任务名称")
    description: str | None = Field(default=None, max_length=512)
    app_id: str = Field(..., max_length=64, description="目标应用 ID")
    app_type: int = Field(..., description="应用类型（FlowType: assistant=5 / workflow=10）")
    schedule_type: str = Field(default="cron", description="cron | once")
    cron_expression: str | None = Field(default=None, description="周期表达式（cron 时必填）")
    run_at: datetime | None = Field(default=None, description="一次性任务的绝对时间（服务器时区）")
    service_account_id: int = Field(..., description="运行身份所属服务账号")
    preset_inputs: list[PresetInputItem] | None = Field(default=None, description="预设输入")


class ScheduledTaskUpdateReq(BaseModel):
    """局部更新：只改传了的字段。运行身份变更会重走创建期校验（AC-03）。"""

    name: str | None = Field(default=None, max_length=128)
    description: str | None = Field(default=None, max_length=512)
    cron_expression: str | None = None
    run_at: datetime | None = None
    schedule_type: str | None = None
    service_account_id: int | None = None
    preset_inputs: list[PresetInputItem] | None = None


class CronPreviewReq(BaseModel):
    expression: str = Field(..., description="待校验/预览的 cron 表达式")


class CronPreviewResp(BaseModel):
    expression: str = Field(..., description="归一化后的表达式")
    description: str = Field(..., description="人类可读解释")
    next_runs: list[datetime] = Field(default_factory=list, description="未来至多 N 次运行时间")


class ScheduledTaskItem(BaseModel):
    """管理面列表项：只暴露任务列表与编辑弹窗需要的信息。"""

    id: int
    name: str
    description: str | None = None
    app_id: str
    app_type: int
    schedule_type: str
    cron_expression: str | None = None
    run_at: datetime | None = None
    schedule_description: str = Field(default="", description="人类可读的触发计划")
    status: str
    next_run_at: datetime | None = None
    last_status: str | None = None
    last_run_at: datetime | None = None
    last_finish_at: datetime | None = None
    last_duration_ms: int | None = None
    last_error: str | None = None
    last_trace_id: str | None = None
    last_app_version_id: str | None = None
    blocked_reason: str | None = None
    consecutive_fail_count: int = 0
    service_account_id: int
    run_identity_user_id: int
    created_by: int
    created_at: datetime | None = None


class AvailableIdentityItem(BaseModel):
    """「可用运行身份」只读清单：不含任何密钥/凭据信息（AC-24）。"""

    service_account_id: int
    name: str
    owner_user_id: int
    enabled: bool
    has_use_app: bool
    usable: bool


class AvailableIdentitiesResp(BaseModel):
    data: list[AvailableIdentityItem]


class NlDraftReq(BaseModel):
    """自然语言生成草稿的请求：绑定当前正在编辑的应用。"""

    app_id: str = Field(..., max_length=64)
    app_type: int
    text: str = Field(..., description="自然语言描述（≤ nl_max_chars）")


class NlDraftResp(BaseModel):
    """草稿（**不落库**）：确认页据此回显可编辑的计划、时间与输入。"""

    schedule_type: str
    cron_expression: str | None = None
    run_at: datetime | None = None
    schedule_description: str
    next_runs: list[datetime] = Field(default_factory=list)
    preset_inputs: list[PresetInputItem] | None = None
    rationale: str = ""
