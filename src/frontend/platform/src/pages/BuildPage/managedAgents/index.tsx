import { bsConfirm } from "@/components/bs-ui/alertDialog/useConfirm"
import { Badge } from "@/components/bs-ui/badge"
import { Button } from "@/components/bs-ui/button"
import { Dialog, DialogContent, DialogHeader, DialogTitle } from "@/components/bs-ui/dialog"
import { SearchInput } from "@/components/bs-ui/input"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/bs-ui/select"
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/bs-ui/table"
import {
    deleteManagedAgentApi,
    healthCheckManagedAgentApi,
    listManagedAgentsApi,
    managedAgentStatsApi,
    transitionManagedAgentApi,
} from "@/controllers/API/managedAgent"
import { getAllLabelsApi } from "@/controllers/API/label"
import { captureAndAlertRequestErrorHoc } from "@/controllers/request"
import type { ManagedAgent, ManagedAgentStats } from "@/types/api/managedAgent"
import { useContext, useEffect, useState } from "react"
import { useTranslation } from "react-i18next"
import { Navigate } from "react-router-dom"
import { locationContext } from "@/contexts/locationContext"
import { ManagedAgentSchedulePanel } from "@/components/bs-comp/apiComponent/scheduledTask/ManagedAgentSchedulePanel"
import { AgentFormDialog } from "./AgentFormDialog"
import { TaxonomyDialog } from "./TaxonomyDialog"

const PAGE_SIZE = 20

function statusVariant(status: ManagedAgent["status"]): "success" | "secondary" | "gray" | "error" {
    if (status === "online") return "success"
    if (status === "draft") return "secondary"
    if (status === "offline") return "gray"
    return "error"
}

function healthVariant(status: ManagedAgent["health_status"]): "success" | "error" | "gray" {
    if (status === "healthy") return "success"
    if (status === "unhealthy") return "error"
    return "gray"
}

export default function ManagedAgentsPage() {
    const { t } = useTranslation("managedAgent")
    const { appConfig } = useContext(locationContext)
    const [rows, setRows] = useState<ManagedAgent[]>([])
    const [total, setTotal] = useState(0)
    const [page, setPage] = useState(1)
    const [keyword, setKeyword] = useState("")
    const [sourceKind, setSourceKind] = useState("all")
    const [status, setStatus] = useState("all")
    const [tagId, setTagId] = useState("all")
    const [tags, setTags] = useState<{ id: number; name: string }[]>([])
    const [loading, setLoading] = useState(false)
    const [formOpen, setFormOpen] = useState(false)
    const [editing, setEditing] = useState<ManagedAgent | null>(null)
    const [detail, setDetail] = useState<ManagedAgent | null>(null)
    const [stats, setStats] = useState<ManagedAgentStats | null>(null)
    const [taxonomyOpen, setTaxonomyOpen] = useState(false)

    const load = () => {
        setLoading(true)
        captureAndAlertRequestErrorHoc(
            listManagedAgentsApi({
                keyword: keyword.trim() || undefined,
                source_kind: sourceKind === "all" ? undefined : sourceKind,
                status: status === "all" ? undefined : status,
                tag_id: tagId === "all" ? undefined : Number(tagId),
                page,
                page_size: PAGE_SIZE,
            }),
        )
            .then((res) => {
                if (!res) return
                setRows(res.data || [])
                setTotal(res.total || 0)
            })
            .finally(() => setLoading(false))
    }

    useEffect(() => {
        load()
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [page, sourceKind, status, tagId])

    useEffect(() => {
        captureAndAlertRequestErrorHoc(getAllLabelsApi()).then((res: { data?: { id: number; name: string }[] }) =>
            setTags(res?.data || []),
        )
    }, [])

    useEffect(() => {
        if (!detail) {
            setStats(null)
            return
        }
        captureAndAlertRequestErrorHoc(managedAgentStatsApi(detail.id)).then((res) => setStats(res || null))
    }, [detail])

    const transition = async (agent: ManagedAgent, action: "publish" | "offline" | "disable") => {
        await captureAndAlertRequestErrorHoc(transitionManagedAgentApi(agent.id, action))
        load()
    }

    const checkHealth = async (agent: ManagedAgent) => {
        await captureAndAlertRequestErrorHoc(healthCheckManagedAgentApi(agent.id))
        load()
    }

    const remove = (agent: ManagedAgent) => {
        bsConfirm({
            title: t("deleteTitle"),
            desc: t("deleteDesc", { name: agent.name }),
            okTxt: t("common.confirm"),
            canelTxt: t("common.cancel"),
            onOk: async () => {
                await captureAndAlertRequestErrorHoc(deleteManagedAgentApi(agent.id))
                load()
            },
        })
    }

    const pageCount = Math.max(Math.ceil(total / PAGE_SIZE), 1)

    // The deployment switch is the only gate for a direct URL; the menu/tab is
    // hidden separately in HeaderMenu.
    if (!appConfig.managedAgentEnabled) {
        return <Navigate to="/build/apps" replace />
    }

    return (
        <div className="h-full overflow-y-auto p-6">
            <div className="mb-4 flex items-center justify-between">
                <div>
                    <h1 className="text-xl font-bold">{t("title")}</h1>
                    <p className="text-sm text-muted-foreground">{t("subtitle")}</p>
                </div>
                <div className="flex gap-2">
                    <Button variant="outline" onClick={() => setTaxonomyOpen(true)}>{t("taxonomyButton")}</Button>
                    <Button onClick={() => { setEditing(null); setFormOpen(true) }}>{t("create")}</Button>
                </div>
            </div>

            <div className="mb-4 flex gap-2">
                <SearchInput
                    className="max-w-xs"
                    value={keyword}
                    placeholder={t("searchPlaceholder")}
                    onChange={(e) => setKeyword(e.target.value)}
                    onKeyDown={(e) => { if (e.key === "Enter") { setPage(1); load() } }}
                />
                <Select value={sourceKind} onValueChange={(v) => { setSourceKind(v); setPage(1) }}>
                    <SelectTrigger className="w-36"><SelectValue /></SelectTrigger>
                    <SelectContent>
                        <SelectItem value="all">{t("source.all")}</SelectItem>
                        <SelectItem value="native">{t("source.native")}</SelectItem>
                        <SelectItem value="low_code">{t("source.low_code")}</SelectItem>
                        <SelectItem value="high_code">{t("source.high_code")}</SelectItem>
                    </SelectContent>
                </Select>
                <Select value={status} onValueChange={(v) => { setStatus(v); setPage(1) }}>
                    <SelectTrigger className="w-36"><SelectValue /></SelectTrigger>
                    <SelectContent>
                        <SelectItem value="all">{t("status.all")}</SelectItem>
                        <SelectItem value="draft">{t("status.draft")}</SelectItem>
                        <SelectItem value="online">{t("status.online")}</SelectItem>
                        <SelectItem value="offline">{t("status.offline")}</SelectItem>
                        <SelectItem value="disabled">{t("status.disabled")}</SelectItem>
                    </SelectContent>
                </Select>
                <Select value={tagId} onValueChange={(v) => { setTagId(v); setPage(1) }}>
                    <SelectTrigger className="w-36"><SelectValue /></SelectTrigger>
                    <SelectContent>
                        <SelectItem value="all">{t("tags.all")}</SelectItem>
                        {tags.map((tag) => <SelectItem key={tag.id} value={String(tag.id)}>{tag.name}</SelectItem>)}
                    </SelectContent>
                </Select>
                <Button variant="outline" onClick={() => { setPage(1); load() }}>{t("refresh")}</Button>
            </div>

            <Table>
                <TableHeader>
                    <TableRow>
                        <TableHead>{t("columns.name")}</TableHead>
                        <TableHead>{t("columns.source")}</TableHead>
                        <TableHead>{t("columns.status")}</TableHead>
                        <TableHead>{t("columns.health")}</TableHead>
                        <TableHead>{t("columns.classification")}</TableHead>
                        <TableHead>{t("columns.tags")}</TableHead>
                        <TableHead>{t("columns.lastCall")}</TableHead>
                        <TableHead className="text-right">{t("columns.actions")}</TableHead>
                    </TableRow>
                </TableHeader>
                <TableBody>
                    {rows.map((agent) => (
                        <TableRow key={agent.id}>
                            <TableCell>
                                <button className="font-medium hover:underline" onClick={() => setDetail(agent)}>{agent.name}</button>
                                <div className="text-xs text-muted-foreground">{agent.protocol}</div>
                            </TableCell>
                            <TableCell>{t(`source.${agent.source_kind}`)}</TableCell>
                            <TableCell><Badge variant={statusVariant(agent.status)}>{t(`status.${agent.status}`)}</Badge></TableCell>
                            <TableCell><Badge variant={healthVariant(agent.health_status)}>{t(`health.${agent.health_status}`)}</Badge></TableCell>
                            <TableCell className="text-sm">
                                {agent.scenario_name || agent.scenario_code || "—"} / {agent.industry_name || agent.industry_code || "—"}
                            </TableCell>
                            <TableCell className="text-sm">
                                {(agent.tag_names || []).map((name) => (
                                    <Badge key={name} variant="secondary" className="mr-1">{name}</Badge>
                                ))}
                                {!(agent.tag_names || []).length && "—"}
                            </TableCell>
                            <TableCell className="text-sm">
                                {agent.last_call_at || "—"}
                                {agent.last_run_status && <span className="ml-2">{agent.last_run_status}</span>}
                            </TableCell>
                            <TableCell className="text-right">
                                <div className="flex flex-wrap justify-end gap-1">
                                    {agent.status !== "online" && <Button size="sm" variant="ghost" onClick={() => transition(agent, "publish")}>{t("actions.publish")}</Button>}
                                    {agent.status === "online" && <Button size="sm" variant="ghost" onClick={() => transition(agent, "offline")}>{t("actions.offline")}</Button>}
                                    {agent.status !== "disabled" && <Button size="sm" variant="ghost" onClick={() => transition(agent, "disable")}>{t("actions.disable")}</Button>}
                                    <Button size="sm" variant="ghost" onClick={() => checkHealth(agent)}>{t("actions.healthCheck")}</Button>
                                    <Button size="sm" variant="ghost" onClick={() => { setEditing(agent); setFormOpen(true) }}>{t("actions.edit")}</Button>
                                    <Button size="sm" variant="ghost" onClick={() => remove(agent)}>{t("actions.delete")}</Button>
                                </div>
                            </TableCell>
                        </TableRow>
                    ))}
                    {!rows.length && !loading && (
                        <TableRow><TableCell colSpan={8} className="text-center text-muted-foreground py-10">{t("noData")}</TableCell></TableRow>
                    )}
                </TableBody>
            </Table>

            <div className="mt-4 flex items-center justify-end gap-2 text-sm">
                <span>{t("pagination", { page, total: pageCount })}</span>
                <Button size="sm" variant="outline" disabled={page <= 1} onClick={() => setPage((p) => p - 1)}>{t("common.prev")}</Button>
                <Button size="sm" variant="outline" disabled={page >= pageCount} onClick={() => setPage((p) => p + 1)}>{t("common.next")}</Button>
            </div>

            <AgentFormDialog
                open={formOpen}
                agent={editing}
                onClose={() => setFormOpen(false)}
                onSaved={load}
            />
            <TaxonomyDialog open={taxonomyOpen} onClose={() => setTaxonomyOpen(false)} />

            <Dialog open={Boolean(detail)} onOpenChange={(next) => !next && setDetail(null)}>
                <DialogContent className="max-w-4xl max-h-[88vh] overflow-y-auto">
                    <DialogHeader><DialogTitle>{detail?.name}</DialogTitle></DialogHeader>
                    {detail && (
                        <div className="space-y-4">
                            <div className="grid grid-cols-2 gap-3 text-sm">
                                <div><span className="text-muted-foreground">{t("columns.source")}: </span>{t(`source.${detail.source_kind}`)} / {detail.protocol}</div>
                                <div><span className="text-muted-foreground">{t("columns.status")}: </span>{t(`status.${detail.status}`)}</div>
                                <div><span className="text-muted-foreground">{t("form.endpoint")}: </span>{detail.endpoint || "—"}</div>
                                <div><span className="text-muted-foreground">{t("form.nativeAppId")}: </span>{detail.native_app_id || "—"}</div>
                                <div><span className="text-muted-foreground">{t("columns.classification")}: </span>{detail.scenario_name || detail.scenario_code || "—"} / {detail.industry_name || detail.industry_code || "—"}</div>
                                <div><span className="text-muted-foreground">{t("form.credentialType")}: </span>{detail.credential_type}{detail.credential_mask ? ` (${detail.credential_mask})` : ""}</div>
                            </div>
                            <div className="grid grid-cols-3 gap-3 border-t pt-4 text-sm">
                                <div className="rounded-md border p-3">
                                    <div className="text-muted-foreground">{t("stats.calls")}</div>
                                    <div className="text-xl font-semibold">{stats ? stats.calls : "—"}</div>
                                </div>
                                <div className="rounded-md border p-3">
                                    <div className="text-muted-foreground">{t("stats.successRate")}</div>
                                    <div className="text-xl font-semibold">{stats ? `${(stats.success_rate * 100).toFixed(1)}%` : "—"}</div>
                                </div>
                                <div className="rounded-md border p-3">
                                    <div className="text-muted-foreground">{t("stats.avgLatency")}</div>
                                    <div className="text-xl font-semibold">{stats?.avg_latency_ms != null ? `${Math.round(stats.avg_latency_ms)} ms` : "—"}</div>
                                </div>
                            </div>
                            <div className="border-t pt-4">
                                <h3 className="mb-2 font-medium">{t("schedule.title")}</h3>
                                <ManagedAgentSchedulePanel agentId={String(detail.id)} />
                            </div>
                        </div>
                    )}
                </DialogContent>
            </Dialog>
        </div>
    )
}
