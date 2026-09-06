// @ts-strict-ignore
import { useEditorDashboardStore } from "@/store/dashboardStore"
import { useEffect, useState } from "react"
import { useTranslation } from "react-i18next"
import { queryCallLogsApi } from "@/controllers/API/dashboard"
import { DashboardComponent } from "../../types/dataConfig"
import { unitConversion } from "../charts/MetricCard"

interface CallLogTableProps {
    component: DashboardComponent
}

interface CallLogRecord {
    type: 'agent_run' | 'model_call'
    app_name?: string
    model_name?: string
    user_name?: string
    status?: string
    process_time?: number
    duration?: number
    time?: number
}

interface CallLogResult {
    records: CallLogRecord[]
    total: number
}

/**
 * F046: agent-run / model-call logs table (raw telemetry events via
 * /api/v1/telemetry/call-logs), filtered by the linked date query component.
 */
export function CallLogTable({ component }: CallLogTableProps) {
    const { t } = useTranslation("dashboard")
    const chartRefreshTriggers = useEditorDashboardStore(state => state.chartRefreshTriggers)
    const queryComponentParams = useEditorDashboardStore(state => state.queryComponentParams)
    const refreshInfo = chartRefreshTriggers[component.id]
    const refreshTrigger = refreshInfo?.trigger || 0

    const [recordType, setRecordType] = useState('')
    const [status, setStatus] = useState('')
    const [page, setPage] = useState(1)
    const [result, setResult] = useState<CallLogResult | null>(null)
    const [loading, setLoading] = useState(false)

    const linkedIds = component.data_config?.linkedComponentIds || []

    useEffect(() => {
        const fetchLogs = async () => {
            setLoading(true)
            try {
                // derive the time window from the linked date query component value
                let startTime: number | undefined
                let endTime: number | undefined
                for (const id of linkedIds) {
                    const p = queryComponentParams?.[id]
                    if (p) {
                        startTime = p.startTime
                        endTime = p.endTime
                        break
                    }
                }
                const data = await queryCallLogsApi({
                    record_type: recordType || undefined,
                    status: status || undefined,
                    start_time: startTime,
                    end_time: endTime,
                    page,
                    page_size: 20,
                })
                setResult(data)
            } finally {
                setLoading(false)
            }
        }
        fetchLogs()
        // eslint-disable-next-line react-hooks/exhaustive-deps -- refetch on filter/page/refresh changes only
    }, [refreshTrigger, recordType, status, page])

    const records = result?.records || []
    const total = result?.total || 0
    const totalPages = Math.max(1, Math.ceil(total / 20))

    const statusBadge = (value: string) => {
        const ok = value === 'success'
        const unknown = value !== 'success' && value !== 'failed'
        const color = unknown ? 'bg-gray-100 text-gray-500' : ok ? 'bg-green-50 text-green-600' : 'bg-red-50 text-red-600'
        const label = unknown ? '—' : ok ? t('callLogSuccess') : t('callLogFailed')
        return <span className={`px-2 py-0.5 rounded text-xs ${color}`}>{label}</span>
    }

    return (
        <div className="w-full h-full flex flex-col gap-2 overflow-hidden">
            <div className="flex items-center gap-3 text-sm">
                <select
                    className="border rounded px-2 py-1 bg-white"
                    value={recordType}
                    onChange={(e) => { setRecordType(e.target.value); setPage(1) }}
                >
                    <option value="">{t('callLogAll')}</option>
                    <option value="agent_run">{t('callLogAgentRun')}</option>
                    <option value="model_call">{t('callLogModelCall')}</option>
                </select>
                <select
                    className="border rounded px-2 py-1 bg-white"
                    value={status}
                    onChange={(e) => { setStatus(e.target.value); setPage(1) }}
                >
                    <option value="">{t('callLogAll')}</option>
                    <option value="success">{t('callLogSuccess')}</option>
                    <option value="failed">{t('callLogFailed')}</option>
                </select>
                <span className="text-gray-400">{t('callLogTotal', { total })}</span>
            </div>
            <div className="flex-1 overflow-auto">
                {loading ? (
                    <div className="w-full h-full flex items-center justify-center text-sm text-gray-400">{t('loading')}</div>
                ) : !records.length ? (
                    <div className="w-full h-full flex items-center justify-center text-sm text-gray-400">{t('noData')}</div>
                ) : (
                    <table className="w-full text-sm border-collapse">
                        <thead>
                            <tr className="text-left text-gray-500 border-b">
                                <th className="px-3 py-2 font-medium">{t('callLogType')}</th>
                                <th className="px-3 py-2 font-medium">{t('callLogUser')}</th>
                                <th className="px-3 py-2 font-medium">{t('callLogStatus')}</th>
                                <th className="px-3 py-2 font-medium">{t('callLogDuration')}</th>
                                <th className="px-3 py-2 font-medium">{t('callLogTime')}</th>
                            </tr>
                        </thead>
                        <tbody>
                            {records.map((r: CallLogRecord, idx: number) => (
                                <tr key={idx} className="border-b border-gray-100 hover:bg-gray-50">
                                    <td className="px-3 py-2 max-w-[280px] truncate">
                                        {r.type === 'agent_run' ? r.app_name : (r.model_name || r.app_name)}
                                    </td>
                                    <td className="px-3 py-2 whitespace-nowrap">{r.user_name}</td>
                                    <td className="px-3 py-2">{statusBadge(r.status)}</td>
                                    <td className="px-3 py-2 whitespace-nowrap">
                                        {r.process_time != null
                                            ? unitConversion(r.process_time, { metrics: [{ numberFormat: { type: 'duration' } }] })[0]
                                            : r.duration != null
                                                ? unitConversion(r.duration, { metrics: [{ numberFormat: { type: 'duration' } }] })[0]
                                                : '—'}
                                    </td>
                                    <td className="px-3 py-2 whitespace-nowrap">
                                        {r.time ? new Date(r.time * 1000).toLocaleString() : '—'}
                                    </td>
                                </tr>
                            ))}
                        </tbody>
                    </table>
                )}
            </div>
            <div className="flex items-center justify-end gap-3 text-sm">
                <button
                    className="px-2 py-1 border rounded disabled:opacity-40"
                    disabled={page <= 1}
                    onClick={() => setPage(p => Math.max(1, p - 1))}
                >
                    {t('callLogPrev')}
                </button>
                <span className="text-gray-500">{page} / {totalPages}</span>
                <button
                    className="px-2 py-1 border rounded disabled:opacity-40"
                    disabled={page >= totalPages}
                    onClick={() => setPage(p => Math.min(totalPages, p + 1))}
                >
                    {t('callLogNext')}
                </button>
            </div>
        </div>
    )
}
