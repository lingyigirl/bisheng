import { Badge } from "@/components/bs-ui/badge"
import { Button } from "@/components/bs-ui/button"
import { bsConfirm } from "@/components/bs-ui/alertDialog/useConfirm"
import { message } from "@/components/bs-ui/toast/use-toast"
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "@/components/bs-ui/tooltip"
import {
    disableScheduledTaskApi,
    enableScheduledTaskApi,
    deleteScheduledTaskApi,
    listScheduledTasksApi,
    runScheduledTaskNowApi,
} from "@/controllers/API/scheduledTask"
import { captureAndAlertRequestErrorHoc } from "@/controllers/request"
import { userContext } from "@/contexts/userContext"
import { ScheduledTaskItem } from "@/types/api/scheduledTask"
import { formatIsoDateTime } from "@/util/utils"
import { useContext, useEffect, useState } from "react"
import { useTranslation } from "react-i18next"
import { useNavigate } from "react-router-dom"
import { formatScheduleDisplay } from "./formatScheduleDisplay"
import { TaskFormDialog } from "./TaskFormDialog"

interface ScheduledTaskPanelProps {
    appId: string
    appType: number
}

const PAGE_SIZE = 20

/** 最近结果 → 徽章样式；失败原因进 Tooltip（列表里不铺开长文案）。 */
function resultVariant(status: ScheduledTaskItem["last_status"]): "success" | "error" | "gray" | "secondary" {
    if (status === "success") return "success"
    if (status === "failed") return "error"
    // `needs_input` is a paused state, not a failure -- use the neutral variant so
    // it does not read as an error in the list.
    if (status === "needs_input") return "secondary"
    return "gray"
}

function durationLabel(ms: number | null): string {
    if (ms === null || ms === undefined) return "—"
    return ms < 1000 ? `${ms}ms` : `${(ms / 1000).toFixed(1)}s`
}

/**
 * 是否存在可归因的真实运行。
 *
 * 计划轮次 FAIL/SKIP 不 claim、不产生运行：`last_run_at`/`last_trace_id` 会停留在
 * 上一次真实运行，`last_duration_ms` 被清空。用“有耗时”作为真实运行的标记，避免
 * 把上一轮的“开始时间/监控”错挂到本轮失败上；`needs_input` 是暂停中的真实运行，
 * 尚未终态回写耗时，单独放行。
 */
function hasMonitorableRun(row: ScheduledTaskItem): boolean {
    return typeof row.last_duration_ms === "number" || row.last_status === "needs_input"
}

/**
 * 「对外发布 → 定时任务」面板（AC-57/AC-58）。
 *
 * 列表列：触发计划 / 下次执行 / 上次结果（失败原因 Tooltip）/ 状态 / 操作。
 * 刷新策略：打开即刷 + 手动刷新（不轮询——运行结果以 list 里的 `last_*` 为准）。
 */
export function ScheduledTaskPanel({ appId, appType }: ScheduledTaskPanelProps) {
    const { t, i18n } = useTranslation("scheduledTask")
    const navigate = useNavigate()
    const { user } = useContext(userContext)
    // AC-52 one-click monitor: hidden when the user cannot open the audit/monitor menu.
    const canViewMonitor = Boolean(
        user?.role === "admin" || user?.is_child_admin || user?.web_menu?.includes("log"),
    )
    const [rows, setRows] = useState<ScheduledTaskItem[]>([])
    const [loading, setLoading] = useState(false)
    const [formOpen, setFormOpen] = useState(false)
    const [editing, setEditing] = useState<ScheduledTaskItem | null>(null)

    const load = () => {
        setLoading(true)
        captureAndAlertRequestErrorHoc(listScheduledTasksApi({ app_id: appId, app_type: appType, page: 1, page_size: PAGE_SIZE }))
            .then((res) => res && setRows(res.data || []))
            .finally(() => setLoading(false))
    }

    useEffect(() => {
        load()
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [appId, appType])

    const openCreate = () => {
        setEditing(null)
        setFormOpen(true)
    }

    const openEdit = (row: ScheduledTaskItem) => {
        setEditing(row)
        setFormOpen(true)
    }

    const toggleEnabled = (row: ScheduledTaskItem) => {
        captureAndAlertRequestErrorHoc((row.status === "enabled" ? disableScheduledTaskApi : enableScheduledTaskApi)(row.id)).then(
            (res) => {
                if (!res) return
                message({ title: t("prompt"), description: t(row.status === "enabled" ? "disableSuccess" : "enableSuccess"), variant: "success" })
                load()
            },
        )
    }

    const remove = (row: ScheduledTaskItem) => {
        bsConfirm({
            title: t("prompt"),
            desc: t("confirmDelete", { name: row.name }),
            okTxt: t("delete"),
            onOk(next) {
                captureAndAlertRequestErrorHoc(deleteScheduledTaskApi(row.id)).then((res) => {
                    if (res) {
                        message({ title: t("prompt"), description: t("deleteSuccess"), variant: "success" })
                        load()
                    }
                    next()
                })
            },
        })
    }

    const runNow = (row: ScheduledTaskItem) => {
        captureAndAlertRequestErrorHoc(runScheduledTaskNowApi(row.id)).then((res) => {
            if (!res) return
            message({ title: t("prompt"), description: t("runAccepted", { trace: res.run_trace_id }), variant: "success" })
            load()
        })
    }

    return (
        <div className="flex h-full flex-col gap-3 p-1">
            <p className="text-sm text-muted-foreground">{t("subtitle")}</p>

            <div className="flex items-center justify-between">
                <Button variant="outline" size="sm" onClick={load} disabled={loading}>
                    {t("refresh")}
                </Button>
                <Button size="sm" className="px-6" onClick={openCreate}>
                    {t("create")}
                </Button>
            </div>

            <div className="min-h-0 flex-1 overflow-y-auto rounded-lg border">
                <table className="w-full text-sm">
                    <thead className="sticky top-0 bg-card">
                        <tr className="text-left text-muted-foreground">
                            <th className="p-2">{t("columns.name")}</th>
                            <th className="p-2">{t("columns.schedule")}</th>
                            <th className="p-2">{t("columns.nextRun")}</th>
                            <th className="p-2">{t("columns.lastResult")}</th>
                            <th className="p-2">{t("columns.status")}</th>
                            <th className="p-2 text-right">{t("columns.actions")}</th>
                        </tr>
                    </thead>
                    <tbody>
                        {loading && rows.length === 0 ? (
                            <tr><td colSpan={6} className="p-4 text-center text-muted-foreground">{t("loading")}</td></tr>
                        ) : rows.length === 0 ? (
                            <tr><td colSpan={6} className="p-4 text-center text-muted-foreground">{t("emptyHint")}</td></tr>
                        ) : (
                            rows.map((row) => (
                                <tr key={row.id} className="border-t">
                                    <td className="p-2">
                                        <div className="flex flex-col">
                                            <span>{row.name}</span>
                                            {row.description && (
                                                <span className="text-xs text-muted-foreground">{row.description}</span>
                                            )}
                                        </div>
                                    </td>
                                    <td className="p-2">
                                        <span>
                                            {formatScheduleDisplay(
                                                t,
                                                i18n.language,
                                                row.schedule_display,
                                                row.schedule_description || row.cron_expression || row.run_at || "—",
                                            )}
                                        </span>
                                    </td>
                                    <td className="p-2 text-muted-foreground">
                                        {row.status === "disabled"
                                            ? "—"
                                            : row.blocked_reason === "app_offline"
                                              ? t("waitingAppOnline")
                                              : row.next_run_at
                                                ? formatIsoDateTime(row.next_run_at)
                                                : "—"}
                                    </td>
                                    <td className="p-2">
                                        {row.last_status ? (
                                            <TooltipProvider>
                                                <Tooltip>
                                                    <TooltipTrigger asChild>
                                                        <span>
                                                            <Badge variant={resultVariant(row.last_status)}>
                                                                {t(`last.${row.last_status}`)}
                                                            </Badge>
                                                        </span>
                                                    </TooltipTrigger>
                                                    <TooltipContent>
                                                        <p>{row.last_error || t("last.noError")}</p>
                                                        {hasMonitorableRun(row) && row.last_run_at && (
                                                            <p>
                                                                {t("last.startedAt", {
                                                                    time: formatIsoDateTime(row.last_run_at),
                                                                })}
                                                            </p>
                                                        )}
                                                        {row.last_status === "failed" && !hasMonitorableRun(row) && (
                                                            <p>{t("last.planFailureHint")}</p>
                                                        )}
                                                    </TooltipContent>
                                                </Tooltip>
                                            </TooltipProvider>
                                        ) : (
                                            <span className="text-muted-foreground">{t("never")}</span>
                                        )}
                                        {(row.last_finish_at || row.last_run_at) && (
                                            <div className="mt-1 text-xs text-muted-foreground">
                                                {[
                                                    // 结果时间优先：计划轮次 FAIL 没有真实运行，
                                                    // last_run_at 可能停留在上一次运行。
                                                    row.last_finish_at
                                                        ? t("last.finishedAt", {
                                                              time: formatIsoDateTime(row.last_finish_at),
                                                          })
                                                        : row.last_run_at
                                                          ? t("last.startedAt", {
                                                                time: formatIsoDateTime(row.last_run_at),
                                                            })
                                                          : "",
                                                    typeof row.last_duration_ms === "number"
                                                        ? t("last.duration", {
                                                              duration: durationLabel(row.last_duration_ms),
                                                          })
                                                        : "",
                                                ]
                                                    .filter(Boolean)
                                                    .join(" · ")}
                                            </div>
                                        )}
                                        {row.last_app_version_id && (
                                            <div className="mt-0.5 text-xs text-muted-foreground">
                                                {t("last.version", { version: row.last_app_version_id })}
                                            </div>
                                        )}
                                    </td>
                                    <td className="p-2">
                                        <Badge variant={row.status === "enabled" ? "success" : "gray"}>
                                            {t(`status.${row.status}`)}
                                        </Badge>
                                    </td>
                                    <td className="p-2 text-right">
                                        {canViewMonitor &&
                                            hasMonitorableRun(row) &&
                                            row.last_trace_id &&
                                            row.last_run_at &&
                                            row.last_status !== "skipped" && (
                                                <Button
                                                    variant="link"
                                                    size="sm"
                                                    onClick={() =>
                                                        navigate(
                                                            `/log?tab=monitor&trace_id=${encodeURIComponent(row.last_trace_id as string)}`,
                                                        )
                                                    }
                                                >
                                                    {t("last.viewMonitor")}
                                                </Button>
                                            )}
                                        <Button variant="link" size="sm" onClick={() => openEdit(row)}>{t("edit")}</Button>
                                        <Button variant="link" size="sm" onClick={() => runNow(row)}>{t("runNow")}</Button>
                                        <Button variant="link" size="sm" onClick={() => toggleEnabled(row)}>
                                            {t(row.status === "enabled" ? "disable" : "enable")}
                                        </Button>
                                        <Button variant="link" size="sm" className="text-red-500" onClick={() => remove(row)}>
                                            {t("delete")}
                                        </Button>
                                    </td>
                                </tr>
                            ))
                        )}
                    </tbody>
                </table>
            </div>

            <TaskFormDialog
                open={formOpen}
                onClose={setFormOpen}
                appId={appId}
                appType={appType}
                task={editing}
                onSaved={load}
            />
        </div>
    )
}
