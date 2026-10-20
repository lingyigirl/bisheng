// @ts-strict-ignore
import { paramsSerializer } from ".";
import axios from "../request";

// 获取操作过组下资源的所有用户
export async function getOperatorsApi(): Promise<[]> {
    return await axios.get('/api/v1/audit/operators')
}

// /api/v1/audit 返回的审计行（兼容 legacy 字段与 v2 结构化字段）。
export interface AuditLogRecord {
    id?: string
    operator_id?: number
    operator_name?: string | null
    create_time?: string | null
    system_id?: string | null
    event_type?: string | null
    object_type?: string | null
    object_name?: string | null
    ip_address?: string | null
    note?: string | null
    action?: string | null
    target_type?: string | null
    target_id?: string | null
    [key: string]: unknown
}

type AuditLogsQuery = {
    userIds?: number[],
    groupId?: string,
    start?: string,
    end?: string,
    moduleId?: string,
    action?: string
}

const buildAuditLogsUrl = ({ page, pageSize, userIds, groupId = '', start, end, moduleId = '', action = '' }: AuditLogsQuery & {
    page: number,
    pageSize: number
}): string => {
    const uids = userIds?.reduce((pre, val) => `${pre}&operator_ids=${val}`, '') || ''
    const startStr = start ? `&start_time=${start}` : ''
    const endStr = end ? `&end_time=${end}` : ''
    return `/api/v1/audit?page=${page}&limit=${pageSize}&group_ids=${groupId}${uids}` +
        `&system_id=${moduleId}&event_type=${action}` + startStr + endStr
}

// 分页获取审计列表
export async function getLogsApi({ page, pageSize, ...filters }: AuditLogsQuery & {
    page: number,
    pageSize: number
}): Promise<{ data: AuditLogRecord[], total: number }> {
    return await axios.get(buildAuditLogsUrl({ ...filters, page, pageSize }))
}

// 导出用：循环分页拉取筛选命中的全部审计日志。
// 复用 /api/v1/audit 的权限与筛选逻辑，避免前后端两套口径。
export async function exportAuditLogsApi(filters: AuditLogsQuery): Promise<AuditLogRecord[]> {
    const pageSize = 1000
    const logs: AuditLogRecord[] = []
    for (let page = 1; ; page++) {
        const res = await axios.get(buildAuditLogsUrl({ ...filters, page, pageSize }))
        const batch = res?.data || []
        logs.push(...batch)
        // 少于一页说明已到末尾；total 兜底防止 count 与分页不一致时死循环。
        if (batch.length < pageSize || logs.length >= (res?.total ?? 0)) break
    }
    return logs
}

// 系统模块
// `tenant` / `llm` are synthetic v2 namespaces (action prefix `tenant.*` /
// `llm.server.*`). Backend `get_audit_logs` maps them to `action LIKE '...'`
// instead of `system_id = ?`. Keep this list in sync with backend
// `_V2_NAMESPACE_TO_ACTION_PREFIX` in audit_log.py.
export async function getModulesApi(): Promise<{ data: any[] }> {
    return {
        data: [
            { name: 'log.systemIdEnum.chat', value: 'chat' },
            { name: 'log.systemIdEnum.build', value: 'build' },
            { name: 'log.systemIdEnum.knowledge', value: 'knowledge' },
            { name: 'log.systemIdEnum.system', value: 'system' },
            { name: 'log.systemIdEnum.dashboard', value: 'dashboard' },
            { name: 'log.systemIdEnum.subscribe', value: 'subscription' },
            { name: 'log.systemIdEnum.knowledgeSpace', value: 'knowledge_space' },
            { name: 'log.systemIdEnum.tenant', value: 'tenant' },
            { name: 'log.systemIdEnum.llm', value: 'llm' },
            { name: 'log.systemIdEnum.approval', value: 'approval' },
            // F058: templates are admin-editable config that silently changes
            // how every later document of that type is extracted, so the module
            // must be filterable here. Mirrors `_V2_NAMESPACE_TO_ACTION_PREFIX`
            // in `database/models/audit_log.py` -- without this entry the rows
            // are written but no filter can reach them.
            { name: 'log.systemIdEnum.docExtract', value: 'doc_extract' },
            // F077: scheduled-task lifecycle. Mirrors the 'schedule' entry in
            // `_V2_NAMESPACE_TO_ACTION_PREFIX` (database/models/audit_log.py);
            // the backend lockstep test asserts both sides agree.
            { name: 'log.systemIdEnum.schedule', value: 'schedule' },
            // F078: managed-agent lifecycle. Mirrors the 'managed_agent' entry
            // in `_V2_NAMESPACE_TO_ACTION_PREFIX`; the backend lockstep test
            // asserts both sides agree.
            { name: 'log.systemIdEnum.managedAgent', value: 'managed_agent' },
        ],

    }
}

const actions = [
    { name: 'log.eventTypeEnum.createChat', value: 'create_chat' },
    { name: 'log.eventTypeEnum.deleteChat', value: 'delete_chat' },
    { name: 'log.eventTypeEnum.createBuild', value: 'create_build' },
    { name: 'log.eventTypeEnum.updateBuild', value: 'update_build' },
    { name: 'log.eventTypeEnum.deleteBuild', value: 'delete_build' },
    { name: 'log.eventTypeEnum.createKnowledge', value: 'create_knowledge' },
    { name: 'log.eventTypeEnum.deleteKnowledge', value: 'delete_knowledge' },
    { name: 'log.eventTypeEnum.uploadFile', value: 'upload_file' },
    { name: 'log.eventTypeEnum.deleteFile', value: 'delete_file' },
    { name: 'log.eventTypeEnum.updateUser', value: 'update_user' },
    { name: 'log.eventTypeEnum.forbidUser', value: 'forbid_user' },
    { name: 'log.eventTypeEnum.recoverUser', value: 'recover_user' },
    { name: 'log.eventTypeEnum.createUserGroup', value: 'create_user_group' },
    { name: 'log.eventTypeEnum.deleteUserGroup', value: 'delete_user_group' },
    { name: 'log.eventTypeEnum.updateUserGroup', value: 'update_user_group' },
    { name: 'log.eventTypeEnum.createRole', value: 'create_role' },
    { name: 'log.eventTypeEnum.deleteRole', value: 'delete_role' },
    { name: 'log.eventTypeEnum.updateRole', value: 'update_role' },
    { name: 'log.eventTypeEnum.userLogin', value: 'user_login' },
    { name: 'log.eventTypeEnum.add_tool', value: 'add_tool' },
    { name: 'log.eventTypeEnum.update_tool', value: 'update_tool' },
    { name: 'log.eventTypeEnum.delete_tool', value: 'delete_tool' },
    { name: 'log.eventTypeEnum.create_dashboard', value: 'create_dashboard' },
    { name: 'log.eventTypeEnum.update_dashboard', value: 'update_dashboard' },
    { name: 'log.eventTypeEnum.delete_dashboard', value: 'delete_dashboard' },
    { name: 'log.eventTypeEnum.create_channel', value: 'create_channel' },
    { name: 'log.eventTypeEnum.delete_channel', value: 'delete_channel' },
    { name: 'log.eventTypeEnum.create_knowledge_space', value: 'create_knowledge_space' },
    { name: 'log.eventTypeEnum.delete_knowledge_space', value: 'delete_knowledge_space' },
    // v2 structured actions surfaced to operators (2026-05-06 product call):
    // tenant lifecycle + LLM server lifecycle only. Keep in sync with backend
    // `_UI_VISIBLE_V2_ACTIONS`. Note: i18n keys use camelCase to avoid
    // colliding with i18next's `.` nesting separator (the on-wire `value`
    // keeps the dotted form expected by the audit_log.action column).
    { name: 'log.eventTypeEnum.tenantMount', value: 'tenant.mount' },
    { name: 'log.eventTypeEnum.tenantUnmount', value: 'tenant.unmount' },
    { name: 'log.eventTypeEnum.tenantDisable', value: 'tenant.disable' },
    { name: 'log.eventTypeEnum.llmServerCreate', value: 'llm.server.create' },
    { name: 'log.eventTypeEnum.llmServerUpdate', value: 'llm.server.update' },
    { name: 'log.eventTypeEnum.llmServerDelete', value: 'llm.server.delete' },
    { name: 'log.eventTypeEnum.approvalRequestSubmit', value: 'approval.request.submit' },
    { name: 'log.eventTypeEnum.approvalTaskApprove', value: 'approval.task.approve' },
    { name: 'log.eventTypeEnum.approvalTaskReject', value: 'approval.task.reject' },
    { name: 'log.eventTypeEnum.approvalRequestWithdraw', value: 'approval.request.withdraw' },
    { name: 'log.eventTypeEnum.approvalRoutePass', value: 'approval.route.pass' },
    { name: 'log.eventTypeEnum.approvalHandlerSuccess', value: 'approval.handler.success' },
    { name: 'log.eventTypeEnum.approvalHandlerFailed', value: 'approval.handler.failed' },
    { name: 'log.eventTypeEnum.approvalExceptionRetry', value: 'approval.exception.retry' },
    { name: 'log.eventTypeEnum.approvalExceptionAssignApprover', value: 'approval.exception.assign_approver' },
    { name: 'log.eventTypeEnum.approvalExceptionCancel', value: 'approval.exception.cancel' },
    { name: 'log.eventTypeEnum.approvalFlowUpdate', value: 'approval.flow.update' },
    { name: 'log.eventTypeEnum.approvalScenarioToggle', value: 'approval.scenario.toggle' },
    { name: 'log.eventTypeEnum.approvalScenarioCreate', value: 'approval.scenario.create' },
    { name: 'log.eventTypeEnum.approvalMenuAccessRevokeGrant', value: 'approval.menu_access.revoke_grant' },
    // F058: document extraction template lifecycle. A template edit silently
    // changes how every later document of that type is extracted, so operators
    // need these in the audit page. Backend mirror:
    // `_UI_VISIBLE_V2_ACTIONS` in `database/models/audit_log.py`; a backend test
    // asserts the two lists agree.
    { name: 'log.eventTypeEnum.docExtractTemplateCreate', value: 'doc_extract.template.create' },
    { name: 'log.eventTypeEnum.docExtractTemplateUpdate', value: 'doc_extract.template.update' },
    { name: 'log.eventTypeEnum.docExtractTemplateDelete', value: 'doc_extract.template.delete' },
    { name: 'log.eventTypeEnum.docExtractTemplateEnable', value: 'doc_extract.template.enable' },
    { name: 'log.eventTypeEnum.docExtractTemplateDisable', value: 'doc_extract.template.disable' },
    // F077: scheduled-task management actions. Editing a task silently changes
    // what every later round does, so these must be reviewable. `schedule.run`
    // is deliberately absent (forensics only, like open_api.call).
    { name: 'log.eventTypeEnum.scheduleTaskCreate', value: 'schedule.task.create' },
    { name: 'log.eventTypeEnum.scheduleTaskUpdate', value: 'schedule.task.update' },
    { name: 'log.eventTypeEnum.scheduleTaskEnable', value: 'schedule.task.enable' },
    { name: 'log.eventTypeEnum.scheduleTaskDisable', value: 'schedule.task.disable' },
    { name: 'log.eventTypeEnum.scheduleTaskDelete', value: 'schedule.task.delete' },
    { name: 'log.eventTypeEnum.scheduleTaskRunNow', value: 'schedule.task.run_now' },
    // F078: managed-agent management actions. `managed_agent.invoke` is
    // deliberately absent (forensics only, like schedule.run).
    { name: 'log.eventTypeEnum.managedAgentCreate', value: 'managed_agent.create' },
    { name: 'log.eventTypeEnum.managedAgentUpdate', value: 'managed_agent.update' },
    { name: 'log.eventTypeEnum.managedAgentDelete', value: 'managed_agent.delete' },
    { name: 'log.eventTypeEnum.managedAgentEnable', value: 'managed_agent.enable' },
    { name: 'log.eventTypeEnum.managedAgentDisable', value: 'managed_agent.disable' },
    { name: 'log.eventTypeEnum.managedAgentHealthCheck', value: 'managed_agent.health_check' },
];

// 全部操作行为
export async function getActionsApi() {
    return actions
}

// 系统模块下操作行为
export async function getActionsByModuleApi(moduleId) {
    switch (moduleId) {
        case 'chat': return actions.filter(a => a.value.includes('chat'))
        case 'build': return actions.filter(a => a.value.includes('build'))
        case 'knowledge': return actions.filter(a => a.value.includes('knowledge') || a.value.includes('file'))
        case 'system': return actions.filter(a => a.value.includes('user') || a.value.includes('role'))
        case 'dashboard': return actions.filter(a => a.value.includes('dashboard'))
        case 'subscription': return actions.filter(a => a.value.includes('channel'))
        case 'knowledge_space': return actions.filter(a => a.value.includes('knowledge_space'))
        case 'tenant': return actions.filter(a => a.value.startsWith('tenant.'))
        case 'llm': return actions.filter(a => a.value.startsWith('llm.server.'))
        case 'approval': return actions.filter(a => a.value.startsWith('approval.'))
        case 'schedule': return actions.filter(a => a.value.startsWith('schedule.'))
        // F058 起就缺的这一档：模块能选、动作下拉却为空（后端白名单与镜像表
        // 都对得上，唯独这里没有分支）。F077 补 case 时顺手补齐。
        case 'doc_extract': return actions.filter(a => a.value.startsWith('doc_extract.'))
        case 'managed_agent': return actions.filter(a => a.value.startsWith('managed_agent.'))
    }
}

// 应用数据标记列表
export async function getChatLabelsApi(params) {
    const { page, pageSize, keyword } = params

    return await axios.get('/api/v1/chat/app/list', {
        params: {
            page_num: page,
            page_size: pageSize,
            keyword
        }
    })
}

// 标注任务列表
export async function getMarksApi({ status, pageSize, page }): Promise<{}> {
    return await axios.get('/api/v1/mark/list', {
        params: {
            page_num: page,
            page_size: pageSize,
            status
        }
    }).then(res => {
        res.data = res.list
        return res
    })
}

// 创建标注任务
export async function createMarkApi(data: { app_list: string[], user_list: number[] }) {
    return await axios.post('/api/v1/mark/create_task', data)
}

// 删除标注任务
export async function deleteMarkApi(task_id) {
    return await axios.delete('/api/v1/mark/del', { params: { task_id } })
}

// 标注会话列表
export async function getMarkChatsApi({ task_id, keyword, page, pageSize, mark_status, mark_user }) {
    return await axios.get('/api/v1/chat/app/list', {
        params: {
            task_id,
            keyword,
            mark_status,
            mark_user: mark_user?.join(','),
            page_num: page,
            page_size: pageSize
        }
    })
}

// 获取用户标注权限
export async function getMarkPermissionApi(): Promise<boolean> {
    return await axios.get('/api/v1/user/mark')
}

// 更新标注状态
export async function updateMarkStatusApi(data: { session_id: string, task_id: number, status: number }) {
    return await axios.post('/api/v1/mark/mark', data)
}

// 获取下一个标注会话
export async function getNextMarkChatApi({ action, chat_id, task_id }) {
    return await axios.get('/api/v1/mark/next', {
        params: {
            action,
            chat_id,
            task_id
        }
    })
}

// 获取会话标注状态
export async function getMarkStatusApi({ chat_id, task_id }) {
    return await axios.get('/api/v1/mark/get_status', {
        params: {
            chat_id,
            task_id
        }
    })
}
/**
 * 获取应用分组列表
 * @param params 请求参数：keyword（关键词）、page（页码）、page_size（每页大小）
 * @param config axios 配置，包含 signal（用于取消请求）
 * @returns 返回分组列表数据
 */
export async function getGroupsApi(
    params: { keyword: string; page: number; page_size: number },
    config?: { signal?: AbortSignal } // 接收 AbortSignal
): Promise<any[]> {
    return await axios.get("/api/v1/group/manage/resources", {
        params, // 请求参数
        signal: config?.signal, // 绑定 AbortSignal
    });
}


// 获取审计应用列表
export async function getAuditAppListApi(params: {
    flow_ids,
    user_ids,
    group_ids,
    start_date,
    end_date,
    feedback,
    sensitive_status,
    page,
    page_size
}) {
    return await axios.get('/api/v1/audit/session', {
        params, paramsSerializer
    })
}

// 导出csv

export async function exportCsvApi(params: {
    flow_ids,
    user_ids,
    group_ids,
    start_date,
    end_date,
    feedback,
    sensitive_status
}) {
    return await axios.get('/api/v1/audit/session/export', {
        params, paramsSerializer
    })
}

// 包装csv的表格数据
export async function exportCsvDataApi(params: {
    flow_ids,
    user_ids,
    group_ids,
    start_date,
    end_date,
    feedback,
    sensitive_status
}) {
    return await axios.get('/api/v1/audit/session/export/data', {
        params, paramsSerializer
    })
}

// ============ F052 智能体监控（agent-monitor tab） ============

export interface AgentTransactionRecord {
    trace_id: string;
    app_id: string;
    app_name: string;
    app_type: string;
    chat_id: string;
    user_id: number;
    user_name: string;
    status: string;
    process_time: number;
    input_snapshot: string | null;
    start_time: number;
    end_time: number;
    time: number;
}

export interface AgentTransactionListResp {
    records: AgentTransactionRecord[];
    total: number;
    page: number;
    page_size: number;
}

export interface TransactionNodeRecord {
    node_id: string;
    node_name: string;
    status: "running" | "success" | "failed";
    start_time: string | null;
    end_time: string | null;
    duration: number | null;
    reason: string | null;
    input_data: unknown;
    output_data: unknown;
    incomplete: boolean;
}

export interface ModelCallRecord {
    model_id: number | null;
    model_name: string;
    status: string | null;
    duration: number;
    first_token_cost_time: number | null;
    total_token: number | null;
    time: number;
}

// 交易列表：一次智能体运行 = 一条交易（F052）
export async function getAgentTransactionsApi(params: {
    page: number;
    page_size: number;
    app_id?: string;
    app_type?: string;
    status?: string;
    keyword?: string;
    start_time?: number;
    end_time?: number;
}): Promise<AgentTransactionListResp> {
    return await axios.get('/api/v1/telemetry/agent-transactions', { params })
}

// 交易详情：节点执行记录（start/end 已合并）
export async function getTransactionNodesApi(traceId: string): Promise<{ trace_id: string; nodes: TransactionNodeRecord[] }> {
    return await axios.get(`/api/v1/telemetry/agent-transactions/${traceId}/nodes`)
}

// 交易详情：该次运行内的模型调用明细（trace_id 关联）
export async function getTransactionModelCallsApi(traceId: string): Promise<{ trace_id: string; records: ModelCallRecord[] }> {
    return await axios.get(`/api/v1/telemetry/agent-transactions/${traceId}/model-calls`)
}

export type ExportFeedback = "all" | "liked" | "exclude_disliked";

// 6.3 导出：当前筛选命中的运行 -> 单轮「问题-答案」微调 JSON。
// silent 交给页面读取 Blob 错误体（超限时后端返回 detail），避免全局提示
// 只显示 "Request failed with status code 400"。
export async function exportAgentTransactionsApi(params: {
    app_id?: string;
    app_type?: string;
    status?: string;
    keyword?: string;
    start_time?: number;
    end_time?: number;
    feedback: ExportFeedback;
    limit?: number;
}): Promise<Blob> {
    // `silent` is read by our request interceptor but is not part of the axios
    // config type; assigning the config to a variable skips excess-property checks.
    const config = { params, responseType: 'blob' as const, silent: true }
    return await axios.get('/api/v1/telemetry/agent-transactions/export', config)
}
