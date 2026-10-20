// F077 定时任务（开放 API 的后台调度面）——管理面类型。
// 字段口径见 features/v2.7.0/077-scheduled-triggers/design.md §4.2。

export type ScheduleType = "cron" | "once"

export interface PresetInputItem {
    key: string
    value: string
}

/** F077 AC-59：后端返回的结构化触发计划（语义 token，前端按语言渲染）。 */
export type RawCronFields = {
    minute: string
    hour: string
    day_of_month: string
    month_of_year: string
    day_of_week: string
}

export type ScheduleDay =
    | { kind: "every_day" }
    | { kind: "weekdays"; weekdays: number[]; range: boolean }
    | { kind: "month_days"; days: number[] }
    | { kind: "yearly_days"; months: number[]; days: number[] }
    | { kind: "yearly_weekdays"; months: number[]; weekdays: number[]; range: boolean }
    | { kind: "every_day_of_months"; months: number[] }
    | { kind: "or"; parts: ScheduleDay[] }

export type ScheduleTime =
    | { kind: "every_minute" }
    | { kind: "every_n_minutes"; n: number }
    | { kind: "minutes_of_hour"; minutes: number[] }
    | { kind: "every_n_hours_at_minute"; n: number; minute: number }
    | { kind: "every_n_minutes_of_hour"; n: number; hour: number }
    | { kind: "every_minute_of_hours"; hours: number[] }
    | { kind: "at"; times: { hour: number; minute: number }[] }
    | { kind: "once"; run_at: string }

export type ScheduleDisplay = { day?: ScheduleDay | null; time: ScheduleTime } | { raw: RawCronFields }

export interface ScheduledTaskItem {
    id: number
    name: string
    description: string | null
    app_id: string
    app_type: number
    schedule_type: ScheduleType
    cron_expression: string | null
    run_at: string | null
    schedule_description: string
    schedule_display: ScheduleDisplay | null
    status: "enabled" | "disabled"
    next_run_at: string | null
    last_status: "success" | "failed" | "skipped" | "needs_input" | null
    last_run_at: string | null
    last_finish_at: string | null
    last_duration_ms: number | null
    last_error: string | null
    last_trace_id: string | null
    last_app_version_id: string | null
    blocked_reason: string | null
    consecutive_fail_count: number
    service_account_id: number
    run_identity_user_id: number
    created_by: number
    created_at: string | null
}

export interface ScheduledTaskCreateReq {
    name: string
    description?: string | null
    app_id: string
    app_type: number
    schedule_type: ScheduleType
    cron_expression?: string | null
    run_at?: string | null
    service_account_id: number
    preset_inputs?: PresetInputItem[] | null
}

export interface ScheduledTaskUpdateReq {
    name?: string
    description?: string | null
    schedule_type?: ScheduleType
    cron_expression?: string | null
    run_at?: string | null
    service_account_id?: number
    preset_inputs?: PresetInputItem[] | null
}

export interface CronPreviewResp {
    expression: string
    description: string
    schedule_display?: ScheduleDisplay | null
    next_runs: string[]
}

export interface AvailableIdentityItem {
    service_account_id: number
    name: string
    owner_user_id: number
    enabled: boolean
    has_use_app: boolean
    usable: boolean
}

export interface NlDraftResp {
    schedule_type: ScheduleType
    cron_expression: string | null
    run_at: string | null
    schedule_description: string
    schedule_display?: ScheduleDisplay | null
    next_runs: string[]
    preset_inputs: PresetInputItem[] | null
    rationale: string
}
