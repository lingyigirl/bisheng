// @ts-strict-ignore
/* eslint-disable @typescript-eslint/no-explicit-any */
import { LoadingIcon } from "@/components/bs-icons/loading"
import { bsConfirm } from "@/components/bs-ui/alertDialog/useConfirm"
import { Button } from "@/components/bs-ui/button"
import { Dialog, DialogContent, DialogHeader, DialogTitle } from "@/components/bs-ui/dialog"
import { SearchInput } from "@/components/bs-ui/input"
import { Progress } from "@/components/bs-ui/progress"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/bs-ui/select"
import { useToast } from "@/components/bs-ui/toast/use-toast"
import {
    buildKnowledgeGraphApi,
    cancelKnowledgeGraphApi,
    clearKnowledgeGraphApi,
    getKnowledgeGraphDataApi,
    getKnowledgeGraphEntityApi,
    getKnowledgeGraphNeighborsApi,
    getKnowledgeGraphOverviewApi,
    getKnowledgeGraphStatusApi,
    searchKnowledgeGraphEntitiesApi,
    type KnowledgeGraphBuildPayload,
    type KnowledgeGraphData,
    type KnowledgeGraphEntityDetail,
    type KnowledgeGraphEntityItem,
    type KnowledgeGraphOverview,
    type KnowledgeGraphStatus,
} from "@/controllers/API/knowledgeGraph"
import { captureAndAlertRequestErrorHoc } from "@/controllers/request"
import { Network, RefreshCw, Trash2, XCircle } from "lucide-react"
import { useCallback, useEffect, useRef, useState } from "react"
import { useTranslation } from "react-i18next"
import { BuildGraphDialog } from "./BuildGraphDialog"
import { EntityDetailPanel } from "./EntityDetailPanel"
import { GraphCanvas } from "./GraphCanvas"

interface KnowledgeGraphDialogProps {
    knowledgeId: number
    canEditKb: boolean
    open: boolean
    onOpenChange: (open: boolean) => void
}

const POLL_INTERVAL = 3000

function isRunning(status: KnowledgeGraphStatus | null): boolean {
    if (!status) return false
    if (status.state === 1) return true
    return !!status.job && [0, 1, 5].includes(status.job.status)
}

export function KnowledgeGraphDialog({
    knowledgeId,
    canEditKb,
    open,
    onOpenChange,
}: KnowledgeGraphDialogProps) {
    const { t } = useTranslation("knowledge")
    const { message } = useToast()

    const [status, setStatus] = useState<KnowledgeGraphStatus | null>(null)
    const [overview, setOverview] = useState<KnowledgeGraphOverview | null>(null)
    const [graph, setGraph] = useState<KnowledgeGraphData | null>(null)
    const [loading, setLoading] = useState(false)
    const [graphLoading, setGraphLoading] = useState(false)
    const [buildOpen, setBuildOpen] = useState(false)
    const [building, setBuilding] = useState(false)
    const [selectedEntityId, setSelectedEntityId] = useState<number | null>(null)
    const [detail, setDetail] = useState<KnowledgeGraphEntityDetail | null>(null)
    const [detailLoading, setDetailLoading] = useState(false)
    const [searchKeyword, setSearchKeyword] = useState("")
    const [searchResults, setSearchResults] = useState<KnowledgeGraphEntityItem[]>([])
    const [typeFilter, setTypeFilter] = useState("__all__")
    const [allTypes, setAllTypes] = useState<string[]>([])

    const pollRef = useRef<ReturnType<typeof setTimeout> | null>(null)
    const searchTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)

    const loadGraph = useCallback(async (types?: string) => {
        setGraphLoading(true)
        const res: any = await captureAndAlertRequestErrorHoc(
            getKnowledgeGraphDataApi(knowledgeId, {
                limit: 200,
                entity_types: types && types !== "__all__" ? types : undefined,
            }),
        )
        setGraphLoading(false)
        if (!res || res === "canceled") return
        setGraph(res)
        if (!types || types === "__all__") {
            setAllTypes(Array.from(new Set<string>(res.nodes.map((node: any) => node.type))))
        }
    }, [knowledgeId])

    const refreshAll = useCallback(async () => {
        const res: any = await captureAndAlertRequestErrorHoc(getKnowledgeGraphStatusApi(knowledgeId))
        if (!res || res === "canceled") return
        setStatus(res)
        if (res.state === 2 || res.state === 4) {
            const ov: any = await captureAndAlertRequestErrorHoc(getKnowledgeGraphOverviewApi(knowledgeId))
            if (ov && ov !== "canceled") setOverview(ov)
            await loadGraph(typeFilter)
        } else {
            setGraph(null)
            setOverview(null)
            setSelectedEntityId(null)
            setDetail(null)
        }
        if (pollRef.current) clearTimeout(pollRef.current)
        if (isRunning(res)) {
            pollRef.current = setTimeout(() => { void refreshAll() }, POLL_INTERVAL)
        }
    }, [knowledgeId, loadGraph, typeFilter])

    useEffect(() => {
        if (!open) {
            if (pollRef.current) clearTimeout(pollRef.current)
            return
        }
        setLoading(true)
        setStatus(null)
        setOverview(null)
        setGraph(null)
        setSelectedEntityId(null)
        setDetail(null)
        setSearchKeyword("")
        setSearchResults([])
        setTypeFilter("__all__")
        void refreshAll().finally(() => setLoading(false))
        return () => {
            if (pollRef.current) clearTimeout(pollRef.current)
        }
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [open, knowledgeId])

    useEffect(() => {
        if (searchTimerRef.current) clearTimeout(searchTimerRef.current)
        const keyword = searchKeyword.trim()
        if (!keyword) {
            setSearchResults([])
            return
        }
        searchTimerRef.current = setTimeout(async () => {
            const res: any = await captureAndAlertRequestErrorHoc(
                searchKnowledgeGraphEntitiesApi(knowledgeId, { keyword, page_size: 10 }),
            )
            if (res && res !== "canceled") setSearchResults(res.data || [])
        }, 300)
        return () => {
            if (searchTimerRef.current) clearTimeout(searchTimerRef.current)
        }
    }, [searchKeyword, knowledgeId])

    const selectEntity = useCallback(async (entityId: number) => {
        setSelectedEntityId(entityId)
        setDetailLoading(true)
        const res: any = await captureAndAlertRequestErrorHoc(
            getKnowledgeGraphEntityApi(knowledgeId, entityId),
        )
        setDetailLoading(false)
        if (!res || res === "canceled") return
        setDetail(res)
    }, [knowledgeId])

    const exploreEntity = useCallback(async (entityId: number) => {
        const res: any = await captureAndAlertRequestErrorHoc(
            getKnowledgeGraphNeighborsApi(knowledgeId, entityId),
        )
        if (res && res !== "canceled") {
            setGraph(res)
            setTypeFilter("__all__")
        }
        void selectEntity(entityId)
    }, [knowledgeId, selectEntity])

    const handleBuild = async (payload: KnowledgeGraphBuildPayload) => {
        setBuilding(true)
        const res: any = await captureAndAlertRequestErrorHoc(
            buildKnowledgeGraphApi(knowledgeId, payload),
        )
        setBuilding(false)
        if (!res || res === "canceled") return
        setBuildOpen(false)
        message({ variant: "success", description: t("knowledgeGraph.buildStarted") })
        void refreshAll()
    }

    const handleCancel = async () => {
        const res: any = await captureAndAlertRequestErrorHoc(cancelKnowledgeGraphApi(knowledgeId))
        if (!res || res === "canceled") return
        message({ variant: "success", description: t("knowledgeGraph.cancelRequested") })
        void refreshAll()
    }

    const handleClear = () => {
        bsConfirm({
            title: t("knowledgeGraph.clearConfirmTitle"),
            desc: t("knowledgeGraph.clearConfirmDesc"),
            onOk(next) {
                captureAndAlertRequestErrorHoc(
                    clearKnowledgeGraphApi(knowledgeId).then(() => {
                        message({ variant: "success", description: t("knowledgeGraph.clearSuccess") })
                        setSelectedEntityId(null)
                        setDetail(null)
                        void refreshAll()
                    }),
                )
                next()
            },
        })
    }

    const handleTypeFilter = (value: string) => {
        setTypeFilter(value)
        void loadGraph(value)
    }

    const handleSearchSelect = (entity: KnowledgeGraphEntityItem) => {
        setSearchKeyword("")
        setSearchResults([])
        void exploreEntity(entity.id)
    }

    const job = status?.job
    // Chunk progress is the useful signal for a single large file; fall back
    // to file progress when the chunk count is unavailable.
    const percent = job?.total_chunks
        ? Math.min(100, Math.round(((job.done_chunks || 0) / job.total_chunks) * 100))
        : job?.total_files
            ? Math.min(100, Math.round(((job.done_files + job.failed_files) / job.total_files) * 100))
            : 0
    const running = isRunning(status)
    const built = status?.state === 2 || status?.state === 4

    const renderEmpty = (failed = false) => (
        <div className="flex h-full flex-col items-center justify-center gap-3 px-6 text-center">
            <Network size={40} className="text-[#C9CDD4]" />
            <div className="text-[15px] font-medium text-[#212121]">
                {failed ? t("knowledgeGraph.buildFailed") : t("knowledgeGraph.emptyTitle")}
            </div>
            <div className="max-w-[460px] text-[12px] leading-5 text-[#86909c]">
                {failed && job?.error_message ? job.error_message : t("knowledgeGraph.emptyDesc")}
            </div>
            {canEditKb ? (
                <Button className="mt-1" onClick={() => setBuildOpen(true)}>
                    {failed ? t("knowledgeGraph.regenerate") : t("knowledgeGraph.generate")}
                </Button>
            ) : (
                <div className="text-[12px] text-[#86909c]">{t("knowledgeGraph.needEditPermission")}</div>
            )}
        </div>
    )

    const renderProgress = () => (
        <div className="flex h-full flex-col items-center justify-center px-10">
            <div className="w-full max-w-[560px] rounded-lg border border-[#EBECF0] bg-white p-5">
                <div className="mb-3 flex items-center gap-2 text-[15px] font-medium text-[#212121]">
                    <LoadingIcon className="size-4" />
                    {t("knowledgeGraph.generating")}
                </div>
                <Progress value={percent} />
                <div className="mt-3 grid grid-cols-2 gap-y-1 text-[12px] leading-5 text-[#4E5969]">
                    <span>{t("knowledgeGraph.fileProgress", {
                        done: job?.done_files ?? 0,
                        total: job?.total_files ?? 0,
                    })}</span>
                    <span>{t("knowledgeGraph.failedFiles", { count: job?.failed_files ?? 0 })}</span>
                    <span>{t("knowledgeGraph.chunkProgress", {
                        done: job?.done_chunks ?? 0,
                        total: job?.total_chunks ?? 0,
                    })}</span>
                    <span>{t("knowledgeGraph.tokenUsage", {
                        input: job?.token_in ?? 0,
                        output: job?.token_out ?? 0,
                    })}</span>
                </div>
                {canEditKb && (
                    <div className="mt-4 flex justify-end">
                        <Button variant="outline" size="sm" onClick={() => void handleCancel()}>
                            <XCircle size={14} className="mr-1" />
                            {t("knowledgeGraph.cancelBuild")}
                        </Button>
                    </div>
                )}
            </div>
        </div>
    )

    const renderGraph = () => (
        <div className="flex h-full flex-col">
            {(status?.stale || status?.state === 4) && (
                <div className="flex items-center justify-between gap-3 border-b border-[#FFE7BA] bg-[#FFFBE6] px-4 py-2 text-[12px] text-[#d46b08]">
                    <span>{t("knowledgeGraph.stale")}</span>
                    {canEditKb && (
                        <Button size="sm" variant="outline" onClick={() => setBuildOpen(true)}>
                            {t("knowledgeGraph.regenerate")}
                        </Button>
                    )}
                </div>
            )}
            <div className="flex flex-wrap items-center gap-2 border-b border-[#EBECF0] px-4 py-2">
                <div className="relative w-[240px]">
                    <SearchInput
                        placeholder={t("knowledgeGraph.searchPlaceholder")}
                        value={searchKeyword}
                        onChange={(event) => setSearchKeyword(event.target.value)}
                    />
                    {searchResults.length > 0 && (
                        <div className="absolute left-0 top-full z-30 mt-1 max-h-[260px] w-full overflow-y-auto rounded-md border border-[#EBECF0] bg-white py-1 shadow-lg">
                            {searchResults.map((entity) => (
                                <button
                                    key={entity.id}
                                    type="button"
                                    className="flex w-full items-center justify-between gap-2 px-3 py-1.5 text-left text-[12px] hover:bg-[#F7F8FA]"
                                    onClick={() => handleSearchSelect(entity)}
                                >
                                    <span className="truncate text-[#212121]">{entity.name}</span>
                                    <span className="shrink-0 text-[#86909c]">{entity.type}</span>
                                </button>
                            ))}
                        </div>
                    )}
                </div>
                <Select value={typeFilter} onValueChange={handleTypeFilter}>
                    <SelectTrigger className="h-9 w-[150px]">
                        <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                        <SelectItem value="__all__">{t("knowledgeGraph.allTypes")}</SelectItem>
                        {allTypes.map((type) => (
                            <SelectItem key={type} value={type}>{type}</SelectItem>
                        ))}
                    </SelectContent>
                </Select>
                <Button variant="outline" className="h-9 px-3" onClick={() => void loadGraph(typeFilter)}>
                    <RefreshCw size={14} />
                </Button>
                <div className="ml-auto flex items-center gap-3 text-[12px] text-[#86909c]">
                    {overview && (
                        <span>
                            {t("knowledgeGraph.counts", {
                                entities: overview.entity_count,
                                relations: overview.relation_count,
                            })}
                        </span>
                    )}
                    {canEditKb && (
                        <>
                            <Button variant="outline" className="h-8 px-3" onClick={() => setBuildOpen(true)}>
                                {t("knowledgeGraph.regenerate")}
                            </Button>
                            <Button variant="outline" className="h-8 px-3 text-red-500" onClick={handleClear}>
                                <Trash2 size={14} className="mr-1" />
                                {t("knowledgeGraph.clear")}
                            </Button>
                        </>
                    )}
                </div>
            </div>
            <div className="flex min-h-0 flex-1">
                <div className="relative min-w-0 flex-1">
                    {graphLoading && (
                        <div className="absolute inset-0 z-10 flex items-center justify-center bg-white/60">
                            <LoadingIcon />
                        </div>
                    )}
                    {graph && graph.nodes.length > 0 ? (
                        <GraphCanvas graph={graph} onNodeClick={selectEntity} />
                    ) : (
                        <div className="flex h-full items-center justify-center text-[12px] text-[#86909c]">
                            {t("knowledgeGraph.noGraphData")}
                        </div>
                    )}
                    {graph?.truncated && (
                        <div className="absolute bottom-2 left-2 z-10 rounded bg-white/90 px-2 py-1 text-[11px] text-[#86909c] shadow">
                            {t("knowledgeGraph.truncated")}
                        </div>
                    )}
                </div>
                <div className="w-[330px] shrink-0 border-l border-[#EBECF0] bg-white">
                    <EntityDetailPanel
                        detail={detail}
                        loading={detailLoading}
                        selectedEntityId={selectedEntityId}
                        onSelectEntity={selectEntity}
                        onExplore={exploreEntity}
                        onClose={() => { setSelectedEntityId(null); setDetail(null) }}
                    />
                </div>
            </div>
        </div>
    )

    return (
        <>
            <Dialog open={open} onOpenChange={onOpenChange}>
                <DialogContent className="flex h-[82vh] w-[1180px] max-w-[96vw] flex-col gap-0 border-none bg-background-login p-0">
                    <DialogHeader className="flex shrink-0 flex-row items-center justify-between border-b border-[#EBECF0] px-5 py-3">
                        <div>
                            <DialogTitle className="text-[16px] font-medium leading-6 text-[#212121]">
                                {t("knowledgeGraph.title")}
                            </DialogTitle>
                            <div className="mt-1 text-[12px] text-[#86909c]">
                                {built
                                    ? t("knowledgeGraph.builtAt", {
                                        time: status?.graph_built_at
                                            ? new Date(status.graph_built_at).toLocaleString()
                                            : "-",
                                    })
                                    : t("knowledgeGraph.description")}
                            </div>
                        </div>
                        <div className="flex items-center gap-2">
                            {running && canEditKb && (
                                <Button variant="outline" size="sm" onClick={() => void handleCancel()}>
                                    <XCircle size={14} className="mr-1" />
                                    {t("knowledgeGraph.cancelBuild")}
                                </Button>
                            )}
                            {!running && canEditKb && (
                                <Button size="sm" onClick={() => setBuildOpen(true)}>
                                    <Network size={14} className="mr-1" />
                                    {built ? t("knowledgeGraph.regenerate") : t("knowledgeGraph.generate")}
                                </Button>
                            )}
                        </div>
                    </DialogHeader>
                    <div className="min-h-0 flex-1">
                        {loading ? (
                            <div className="flex h-full items-center justify-center"><LoadingIcon /></div>
                        ) : !status ? (
                            <div className="flex h-full items-center justify-center text-[12px] text-[#86909c]">
                                {t("knowledgeGraph.loadFailed")}
                            </div>
                        ) : running ? (
                            renderProgress()
                        ) : status.state === 3 ? (
                            renderEmpty(true)
                        ) : built ? (
                            renderGraph()
                        ) : (
                            renderEmpty(false)
                        )}
                    </div>
                </DialogContent>
            </Dialog>

            <BuildGraphDialog
                open={buildOpen}
                onOpenChange={setBuildOpen}
                isRebuild={built}
                loading={building}
                onSubmit={handleBuild}
            />
        </>
    )
}
