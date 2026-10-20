// F077 定时任务管理面 API（platform）。
// 一律走 @/controllers/request 包装（含 api_errors:<code> 的三语错误文案与 403 拦截），
// 业务代码不写 403 分支（根 AGENTS.md §4）。

import axios from "@/controllers/request"
import {
    AvailableIdentityItem,
    CronPreviewResp,
    NlDraftResp,
    ScheduledTaskCreateReq,
    ScheduledTaskItem,
    ScheduledTaskUpdateReq,
} from "@/types/api/scheduledTask"

export async function listScheduledTasksApi(params: {
    app_id: string
    app_type: number
    page?: number
    page_size?: number
    keyword?: string
    status?: string
}): Promise<{ data: ScheduledTaskItem[]; total: number }> {
    return await axios.get("/api/v1/scheduled-tasks", { params })
}

export async function createScheduledTaskApi(req: ScheduledTaskCreateReq): Promise<ScheduledTaskItem> {
    return await axios.post("/api/v1/scheduled-tasks", req)
}

export async function updateScheduledTaskApi(id: number, req: ScheduledTaskUpdateReq): Promise<ScheduledTaskItem> {
    return await axios.put(`/api/v1/scheduled-tasks/${id}`, req)
}

export async function enableScheduledTaskApi(id: number): Promise<ScheduledTaskItem> {
    return await axios.post(`/api/v1/scheduled-tasks/${id}/enable`)
}

export async function disableScheduledTaskApi(id: number): Promise<ScheduledTaskItem> {
    return await axios.post(`/api/v1/scheduled-tasks/${id}/disable`)
}

export async function deleteScheduledTaskApi(id: number): Promise<{ deleted: boolean }> {
    return await axios.delete(`/api/v1/scheduled-tasks/${id}`)
}

/** 立即运行：返回本次运行的 trace id（可据此在监控里核对）。 */
export async function runScheduledTaskNowApi(id: number): Promise<{ run_trace_id: string }> {
    return await axios.post(`/api/v1/scheduled-tasks/${id}/run`)
}

export async function previewCronApi(expression: string): Promise<CronPreviewResp> {
    return await axios.post("/api/v1/scheduled-tasks/preview", { expression })
}

/** 「可用运行身份」只读清单：不含任何密钥信息（AC-24）。 */
export async function listAvailableIdentitiesApi(params: {
    app_id: string
    app_type: number
}): Promise<{ data: AvailableIdentityItem[] }> {
    return await axios.get("/api/v1/scheduled-tasks/available-identities", { params })
}

/** 自然语言 → 草稿（不落库）；确认后由 create 落库（AC-42）。 */
export async function draftScheduledTaskApi(req: {
    app_id: string
    app_type: number
    text: string
}): Promise<NlDraftResp> {
    return await axios.post("/api/v1/scheduled-tasks/nl-draft", req)
}
