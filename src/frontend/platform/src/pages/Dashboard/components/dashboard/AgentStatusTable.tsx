// @ts-strict-ignore
import { useEditorDashboardStore } from "@/store/dashboardStore"
import { useEffect, useMemo, useState } from "react"
import { useTranslation } from "react-i18next"
import axios from "@/controllers/request"
import { DashboardComponent } from "../../types/dataConfig"
import { unitConversion } from "../charts/MetricCard"

type CellValue = string | number | null

interface AgentStatusTableProps {
    component: DashboardComponent
    isPreviewMode: boolean
}

/**
 * F046: per-agent run status table, backed by the component/query engine
 * (mid_agent_run_dtl dataset: app dimension + run metrics).
 */
export function AgentStatusTable({ component, isPreviewMode }: AgentStatusTableProps) {
    const { t } = useTranslation("dashboard")
    const currentDashboard = useEditorDashboardStore(state => state.currentDashboard)
    const chartRefreshTriggers = useEditorDashboardStore(state => state.chartRefreshTriggers)
    const refreshInfo = chartRefreshTriggers[component.id]
    const refreshTrigger = refreshInfo?.trigger || 0
    const queryParams = refreshInfo?.queryParams || []

    // eslint-disable-next-line @typescript-eslint/no-explicit-any -- component/query response is an untyped 2D matrix
const [data, setData] = useState<any | null>(null)
    const [isLoading, setIsLoading] = useState(false)

    useEffect(() => {
        const fetchData = async () => {
            setIsLoading(true)
            try {
                const res = await axios.post(`/api/v1/telemetry/dashboard/component/query`, {
                    dashboard_id: currentDashboard?.id,
                    component_data: isPreviewMode ? undefined : component,
                    component_id: isPreviewMode ? component.id : undefined,
                    time_filters: queryParams
                        .filter(p => p.queryComponentParams || (p.queryConditions && p.queryConditions.hasDefaultValue))
                        .map(({ queryComponentParams: p, queryConditions: q }) => {
                            if (p) {
                                return {
                                    type: p.shortcutKey ? 'recent_days' : 'custom',
                                    mode: p.isDynamic ? 'dynamic' : 'fixed',
                                    recentDays: p.shortcutKey ? Number(String(p.shortcutKey).replace('last_', '')) : undefined,
                                    startDate: p.startTime,
                                    endDate: p.endTime,
                                }
                            }
                            return q.defaultValue
                        }),
                })
                setData(res.data)
            } finally {
                setIsLoading(false)
            }
        }
        fetchData()
        // eslint-disable-next-line react-hooks/exhaustive-deps -- refetch on trigger/component change only
    }, [component.id, refreshTrigger, isPreviewMode])

    const columns = useMemo(() => (component.data_config?.metrics || []), [component.data_config])
    const rows = useMemo(() => {
        const value = data?.value || data?.data?.value || []
        if (!Array.isArray(value)) return []
        return value.map((row: CellValue[]) => row)
    }, [data])

    if (isLoading) {
        return <div className="w-full h-full flex items-center justify-center text-sm text-gray-400">{t('loading')}</div>
    }

    if (!rows.length) {
        return <div className="w-full h-full flex items-center justify-center text-sm text-gray-400">{t('noData')}</div>
    }

    return (
        <div className="w-full h-full overflow-auto">
            <table className="w-full text-sm border-collapse">
                <thead>
                    <tr className="text-left text-gray-500 border-b">
                        {columns.map(col => (
                            <th key={col.fieldId} className="px-3 py-2 font-medium whitespace-nowrap">
                                {col.displayName || col.fieldName}
                            </th>
                        ))}
                    </tr>
                </thead>
                <tbody>
                    {rows.map((row, idx) => (
                        <tr key={idx} className="border-b border-gray-100 hover:bg-gray-50">
                            {row.map((cell, cellIdx) => {
                                const col = columns[cellIdx]
                                if (!col) return <td key={cellIdx} className="px-3 py-2">{String(cell)}</td>
                                const num = typeof cell === 'number' ? cell : Number(cell)
                                const text = Number.isFinite(num) && col.numberFormat
                                    ? unitConversion(num, { metrics: [{ numberFormat: col.numberFormat }] })[0]
                                    : String(cell ?? '')
                                return <td key={cellIdx} className="px-3 py-2 whitespace-nowrap">{text}</td>
                            })}
                        </tr>
                    ))}
                </tbody>
            </table>
        </div>
    )
}
