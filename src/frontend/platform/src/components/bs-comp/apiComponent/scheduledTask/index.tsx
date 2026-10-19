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
import { ScheduledTaskItem } from "@/types/api/scheduledTask"
import { formatIsoDateTime } from "@/util/utils"
import { useEffect, useState } from "react"
import { useTranslation } from "react-i18next"
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

/**
 * 「对外发布 → 定时任务」面板（AC-57/AC-58）。
 *
 * 列表列：触发计划 / 下次执行 / 上次结果（失败原因 Tooltip）/ 状态 / 操作。
 * 刷新策略：打开即刷 + 手动刷新（不轮询——运行结果以 list 里的 `last_*` 为准）。
 */
export function ScheduledTaskPanel({ appId, appType }: ScheduledTaskPanelProps) {
    const { t } = useTranslation("scheduledTask")
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
                                        <span>{row.schedule_description || row.cron_expression || row.run_at}</span>
                                    </td>
                                    <td className="p-2 text-muted-foreground">
                                        {row.blocked_reason === "app_offline"
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
                                                    </TooltipContent>
                                                </Tooltip>
                                            </TooltipProvider>
                                        ) : (
                                            <span className="text-muted-foreground">{t("never")}</span>
                                        )}
                                    </td>
                                    <td className="p-2">
                                        <Badge variant={row.status === "enabled" ? "success" : "gray"}>
                                            {t(`status.${row.status}`)}
                                        </Badge>
                                    </td>
                                    <td className="p-2 text-right">
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
