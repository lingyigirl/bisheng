import { Badge } from "@/components/bs-ui/badge"
import { Button } from "@/components/bs-ui/button"
import { Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle } from "@/components/bs-ui/dialog"
import { SearchInput } from "@/components/bs-ui/input"
import AutoPagination from "@/components/bs-ui/pagination/autoPagination"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/bs-ui/tabs"
import {
    Table,
    TableBody,
    TableCell,
    TableHead,
    TableHeader,
    TableRow,
} from "@/components/bs-ui/table"
import { bsConfirm } from "@/components/bs-ui/alertDialog/useConfirm"
import { message } from "@/components/bs-ui/toast/use-toast"
import { PlusIcon } from "@/components/bs-icons"
import {
    deleteServiceAccountApi,
    disableServiceAccountApi,
    enableServiceAccountApi,
    listServiceAccountsApi,
} from "@/controllers/API/serviceAccount"
import { captureAndAlertRequestErrorHoc } from "@/controllers/request"
import { ServiceAccount } from "@/types/api/serviceAccount"
import { formatIsoDateTime } from "@/util/utils"
import { useEffect, useRef, useState } from "react"
import { useTranslation } from "react-i18next"
import { AccessGuideTab } from "./AccessGuideTab"
import { CreateServiceAccountDialog } from "./CreateServiceAccountDialog"
import { DetailView } from "./DetailView"

const PAGE_SIZE = 20

/**
 * System -> Service accounts (F076): list + create, plus the API access guide
 * (the handover material admins used to dictate offline). Detail tabs
 * (keys / grants) land in the follow-up task; this page links nothing that
 * does not exist yet.
 */
export function ServiceAccountPanel() {
    const { t } = useTranslation("serviceAccount")
    const [rows, setRows] = useState<ServiceAccount[]>([])
    const [total, setTotal] = useState(0)
    const [page, setPage] = useState(1)
    const [keyword, setKeyword] = useState("")
    const [loading, setLoading] = useState(false)
    const [createOpen, setCreateOpen] = useState(false)
    // Set right after a successful create to offer the "issue a key" next step.
    const [created, setCreated] = useState<ServiceAccount | null>(null)
    // Non-null = the detail view replaces the list (T-24).
    const [detail, setDetail] = useState<{ id: number; tab: string } | null>(null)
    // Panel-level sub-tab: accounts (list/detail) vs. the access guide.
    const [section, setSection] = useState("accounts")
    // Guards against out-of-order responses when the admin types fast.
    const requestSeq = useRef(0)

    const load = async (nextPage = page, nextKeyword = keyword) => {
        const seq = ++requestSeq.current
        setLoading(true)
        await captureAndAlertRequestErrorHoc(
            listServiceAccountsApi({ page: nextPage, page_size: PAGE_SIZE, keyword: nextKeyword || undefined }).then(
                (res) => {
                    if (seq !== requestSeq.current) return
                    setRows(res.data || [])
                    setTotal(res.total || 0)
                    setPage(nextPage)
                },
            ),
        )
        if (seq === requestSeq.current) setLoading(false)
    }

    useEffect(() => {
        load(1, "")
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [])

    const handleToggleEnabled = (row: ServiceAccount) => {
        const run = () =>
            captureAndAlertRequestErrorHoc((row.enabled ? disableServiceAccountApi : enableServiceAccountApi)(row.id)).then(
                (res) => {
                    if (!res) return
                    message({
                        title: t("prompt"),
                        description: row.enabled ? t("disableSuccess") : t("enableSuccess"),
                        variant: "success",
                    })
                    load()
                },
            )

        if (!row.enabled) {
            run()
            return
        }
        // Disabling revokes every key of the account -- never do it silently.
        bsConfirm({
            title: t("prompt"),
            desc: t("confirm.disableDesc", { name: row.name, count: row.key_count }),
            okTxt: t("confirm.disableOk"),
            onOk(next) {
                run().then(() => next())
            },
        })
    }

    const handleDelete = (row: ServiceAccount) => {
        bsConfirm({
            title: t("prompt"),
            desc: t("confirm.deleteDesc", { name: row.name }),
            okTxt: t("delete"),
            onOk(next) {
                captureAndAlertRequestErrorHoc(deleteServiceAccountApi(row.id)).then((res) => {
                    if (res) {
                        message({ title: t("prompt"), description: t("deleteSuccess"), variant: "success" })
                        // Removing the last row of a page would otherwise leave the
                        // admin staring at an empty page while the previous one is
                        // still full.
                        const targetPage = rows.length === 1 && page > 1 ? page - 1 : page
                        load(targetPage, keyword)
                    }
                    next()
                })
            },
        })
    }

    if (detail) {
        return (
            <DetailView
                accountId={detail.id}
                initialTab={detail.tab}
                onBack={() => {
                    setDetail(null)
                    // Name / status / key count may all have changed in there.
                    load(1, keyword)
                }}
            />
        )
    }

    return (
        <div className="flex h-full w-full flex-col gap-4 overflow-hidden p-4">
            {/* Say what this page governs: "who may call in from outside via API",
                not merely a list of accounts. */}
            <p className="shrink-0 text-sm text-muted-foreground">{t("subtitle")}</p>

            <Tabs value={section} onValueChange={setSection} className="flex min-h-0 w-full flex-1 flex-col">
                <TabsList className="shrink-0 self-start">
                    <TabsTrigger value="accounts">{t("tabs.accounts")}</TabsTrigger>
                    <TabsTrigger value="guide">{t("tabs.guide")}</TabsTrigger>
                </TabsList>

                {/* Radix unmounts the inactive tab's subtree, but every piece of list
                    state (rows/page/keyword) lives on this component, so switching back
                    re-renders from state instead of refetching. */}
                <TabsContent value="accounts" className="mt-3 flex min-h-0 flex-1 flex-col gap-4 overflow-hidden">
                    <div className="flex shrink-0 items-center justify-between gap-4">
                        <SearchInput
                            className="w-[320px] shrink-0"
                            placeholder={t("searchPlaceholder")}
                            value={keyword}
                            onChange={(e) => setKeyword(e.target.value)}
                            onKeyDown={(e) => {
                                if (e.key === "Enter") load(1, keyword)
                            }}
                        />
                        <Button className="px-8" onClick={() => setCreateOpen(true)}>
                            <PlusIcon className="mr-2 h-4 w-4" />
                            {t("create")}
                        </Button>
                    </div>

                    <div className="min-h-0 flex-1 overflow-y-auto rounded-lg border">
                        <Table>
                            <TableHeader>
                                <TableRow>
                                    <TableHead>{t("columns.name")}</TableHead>
                                    <TableHead className="w-[120px]">{t("columns.status")}</TableHead>
                                    <TableHead className="w-[120px]">{t("columns.keyCount")}</TableHead>
                                    <TableHead className="w-[200px]">{t("columns.lastUsed")}</TableHead>
                                    <TableHead className="w-[220px] text-right">{t("columns.actions")}</TableHead>
                                </TableRow>
                            </TableHeader>
                            <TableBody>
                                {loading && rows.length === 0 ? (
                                    <TableRow>
                                        <TableCell colSpan={5} className="text-center text-muted-foreground">
                                            {t("loading")}
                                        </TableCell>
                                    </TableRow>
                                ) : rows.length === 0 ? (
                                    <TableRow>
                                        <TableCell colSpan={5} className="text-center text-muted-foreground">
                                            {t("emptyHint")}
                                        </TableCell>
                                    </TableRow>
                                ) : (
                                    rows.map((row) => (
                                        <TableRow key={row.id}>
                                            <TableCell>
                                                <div className="flex flex-col">
                                                    <span>{row.name}</span>
                                                    {row.description && (
                                                        <span className="text-xs text-muted-foreground">{row.description}</span>
                                                    )}
                                                </div>
                                            </TableCell>
                                            <TableCell>
                                                <Badge variant={row.enabled ? "success" : "gray"}>
                                                    {row.enabled ? t("status.enabled") : t("status.disabled")}
                                                </Badge>
                                            </TableCell>
                                            <TableCell>
                                                <span className={row.key_count === 0 ? "text-red-500" : undefined}>
                                                    {row.key_count}
                                                </span>
                                            </TableCell>
                                            <TableCell className="text-muted-foreground">
                                                {row.last_used_at ? formatIsoDateTime(row.last_used_at) : t("never")}
                                            </TableCell>
                                            <TableCell className="text-right">
                                                <Button
                                                    variant="link"
                                                    size="sm"
                                                    onClick={() => setDetail({ id: row.id, tab: "overview" })}
                                                >
                                                    {t("detailAction")}
                                                </Button>
                                                <Button variant="link" size="sm" onClick={() => handleToggleEnabled(row)}>
                                                    {row.enabled ? t("disable") : t("enable")}
                                                </Button>
                                                <Button
                                                    variant="link"
                                                    size="sm"
                                                    className="text-red-500"
                                                    onClick={() => handleDelete(row)}
                                                >
                                                    {t("delete")}
                                                </Button>
                                            </TableCell>
                                        </TableRow>
                                    ))
                                )}
                            </TableBody>
                        </Table>
                    </div>

                    <div className="flex shrink-0 items-center justify-end">
                        <AutoPagination
                            page={page}
                            pageSize={PAGE_SIZE}
                            total={total}
                            showTotal
                            onChange={(p) => load(p)}
                        />
                    </div>
                </TabsContent>

                <TabsContent value="guide" className="mt-3 flex min-h-0 flex-1 flex-col overflow-hidden">
                    <AccessGuideTab />
                </TabsContent>
            </Tabs>

            <CreateServiceAccountDialog
                open={createOpen}
                onClose={setCreateOpen}
                onCreated={(account) => {
                    setCreated(account)
                    load(1, keyword)
                }}
            />

            {/* Post-create next step: jump straight into the detail view's keys
                tab, which is where the first key gets issued. */}
            <Dialog open={!!created} onOpenChange={(next) => !next && setCreated(null)}>
                <DialogContent className="sm:max-w-[480px]">
                    <DialogHeader>
                        <DialogTitle>{t("createdDialog.title")}</DialogTitle>
                    </DialogHeader>
                    <p className="text-sm text-muted-foreground">{t("createdDialog.nextStep", { name: created?.name ?? "" })}</p>
                    <DialogFooter className="mt-4">
                        <Button variant="outline" className="h-10 w-[120px] px-16" onClick={() => setCreated(null)}>
                            {t("confirm.gotIt")}
                        </Button>
                        <Button
                            className="h-10 w-[160px] px-6"
                            onClick={() => {
                                if (!created) return
                                setDetail({ id: created.id, tab: "keys" })
                                setCreated(null)
                            }}
                        >
                            {t("createdDialog.goToKeys")}
                        </Button>
                    </DialogFooter>
                </DialogContent>
            </Dialog>
        </div>
    )
}
