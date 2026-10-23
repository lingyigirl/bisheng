// 系统操作审计日志的展示 / 导出格式化工具。
// 抽离为纯函数，避免导出逻辑与页面渲染逻辑各写一份，也便于单测。
import type { AuditLogRecord } from "@/controllers/API/log";
import { formatDate } from "@/util/utils";

type TranslateFn = (key: string, opts?: { defaultValue?: string }) => string;
type CsvCell = string | number | null | undefined;

// v2.5.1 audit_log rows from `ainsert_v2()` only fill `action` / `target_type`
// / `target_id` and leave the legacy `system_id` / `event_type` / `object_type`
// columns NULL. Fall back to the structured fields so the page does not render
// the literal i18n key (`log.systemIdEnum.null`). Backend `_UI_VISIBLE_V2_ACTIONS`
// already restricts which v2 rows reach the list — these helpers only need to
// handle that whitelist plus a defensive fallback for unexpected rows.

// Structured action (`tenant.mount`, `approval.exception.assign_approver`) →
// camelCase i18n leaf key (`tenantMount`, `approvalExceptionAssignApprover`).
// Both `.` and `_` act as word separators: i18next treats `.` as a nesting
// separator and locale keys use camelCase throughout, so snake_case segments
// must also be folded into the camelCase form to resolve.
export const actionToI18nKey = (action: string): string => {
    const [head, ...rest] = action.split(/[._]/).filter(Boolean)
    if (!head) return action
    return head + rest.map(p => p.charAt(0).toUpperCase() + p.slice(1)).join('')
}

export const renderSystemId = (log: AuditLogRecord, t: TranslateFn): string => {
    if (log.system_id) return t(`log.systemIdEnum.${log.system_id}`)
    if (log.action) {
        const ns = String(log.action).split('.')[0]
        // Synthetic v2 namespaces are stored snake_case (managed_agent) while
        // the locale keys are camelCase (managedAgent); fold before lookup.
        return t(`log.systemIdEnum.${actionToI18nKey(ns)}`, { defaultValue: ns })
    }
    return '-'
}

export const renderEventType = (log: AuditLogRecord, t: TranslateFn): string => {
    if (log.event_type) return t(`log.eventTypeEnum.${log.event_type}`)
    if (log.action) {
        const key = actionToI18nKey(log.action)
        return t(`log.eventTypeEnum.${key}`, { defaultValue: log.action })
    }
    return '-'
}

export const renderObjectType = (log: AuditLogRecord, t: TranslateFn): string => {
    if (log.object_type) return t(`log.objectTypeEnum.${log.object_type}`)
    if (log.target_type) return t(`log.objectTypeEnum.${log.target_type}`, { defaultValue: log.target_type })
    return t('log.objectTypeEnum.none')
}

export const renderObjectName = (log: AuditLogRecord, t: TranslateFn): string => {
    return log.object_name || log.target_id || t('log.objectTypeEnum.none')
}

const EXPORT_DAY_MS = 24 * 60 * 60 * 1000;
const EXPORT_DEFAULT_SPAN_DAYS = 60;

export type AuditExportRangeNotice = 'default' | 'custom' | null;

export interface AuditExportRange {
    start: string;
    end: string;
    // 'default' 表示两端都未选、按最近 60 天导出；
    // 'custom' 表示只选了一端、自动补齐为 60 天窗口。
    notice: AuditExportRangeNotice;
}

const startOfDay = (date: Date): string => `${formatDate(date, 'yyyy-MM-dd')} 00:00:00`;
const endOfDay = (date: Date): string => `${formatDate(date, 'yyyy-MM-dd')} 23:59:59`;
const addDays = (date: Date, days: number): Date => new Date(date.getTime() + days * EXPORT_DAY_MS);

/**
 * 计算审计日志导出的时间范围。
 *
 * - 开始/结束都未选择：默认最近 60 个自然日（含今天）。
 * - 只选择一端：以已选日期为锚点补齐 60 天窗口，避免意外全量导出。
 * - 两端都选择：按用户选择导出（开始日 00:00:00，结束日 23:59:59）。
 *
 * @param start DatePicker 选择的开始日期
 * @param end DatePicker 选择的结束日期
 * @param now 当前时间（单测注入固定值）
 */
export function resolveAuditExportRange(
    start?: Date | null,
    end?: Date | null,
    now: Date = new Date(),
): AuditExportRange {
    if (!start && !end) {
        const rangeEnd = now;
        const rangeStart = addDays(rangeEnd, -(EXPORT_DEFAULT_SPAN_DAYS - 1));
        return { start: startOfDay(rangeStart), end: endOfDay(rangeEnd), notice: 'default' };
    }
    if (start && !end) {
        return {
            start: startOfDay(start),
            end: endOfDay(addDays(start, EXPORT_DEFAULT_SPAN_DAYS - 1)),
            notice: 'custom',
        };
    }
    if (!start && end) {
        return {
            start: startOfDay(addDays(end, -(EXPORT_DEFAULT_SPAN_DAYS - 1))),
            end: endOfDay(end),
            notice: 'custom',
        };
    }
    return { start: startOfDay(start as Date), end: endOfDay(end as Date), notice: null };
}

/**
 * CSV 公式注入防护：以 = + - @ 开头的单元格在 Excel 中会被当作公式执行，
 * 统一加单引号转义（与日志详情导出保持一致）。
 */
export function sanitizeCsvCell(value: CsvCell): CsvCell {
    if (typeof value !== 'string') return value;
    return /^[=+\-@]/.test(value) ? `'${value}` : value;
}

/**
 * 将审计日志列表转换为 CSV 的二维数组（首行为表头）。
 * 列顺序与「系统操作」表格保持一致。
 */
export function buildAuditExportRows(logs: AuditLogRecord[], t: TranslateFn): CsvCell[][] {
    const rows: CsvCell[][] = [[
        t('log.auditId'),
        t('log.username'),
        t('log.operationTime'),
        t('log.systemModule'),
        t('log.operationAction'),
        t('log.objectType'),
        t('log.operationObject'),
        t('log.ipAddress'),
        t('log.remark'),
    ]];
    for (const log of logs) {
        rows.push([
            log.id,
            sanitizeCsvCell(log.operator_name),
            (log.create_time || '').replace('T', ' '),
            sanitizeCsvCell(renderSystemId(log, t)),
            sanitizeCsvCell(renderEventType(log, t)),
            sanitizeCsvCell(renderObjectType(log, t)),
            sanitizeCsvCell(renderObjectName(log, t)),
            sanitizeCsvCell(log.ip_address),
            sanitizeCsvCell(log.note),
        ]);
    }
    return rows;
}
