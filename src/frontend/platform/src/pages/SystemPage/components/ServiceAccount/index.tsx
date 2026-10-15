import { Badge } from "@/components/bs-ui/badge"
import { Button } from "@/components/bs-ui/button"
import { Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle } from "@/components/bs-ui/dialog"
import { SearchInput } from "@/components/bs-ui/input"
import AutoPagination from "@/components/bs-ui/pagination/autoPagination"
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
import { CreateServiceAccountDialog } from "./CreateServiceAccountDialog"

const PAGE_SIZE = 20

/**
 * System -> Service accounts (F076): list + create. Detail tabs (keys / grants) land in
 * the follow-up task; this page links nothing that does not exist yet.
 */
export function ServiceAccountPanel() {
    const { t } = useTranslation("serviceAccount")
    const [rows, setRows] = useState<ServiceAccount[]>([])
    const [total, setTotal] = useState(0)
    const [page, setPage] = useState(1)
    const [keyword, setKeyword] = useState("")
    const [loading, setLoading] = useState(false)
    const [createOpen, setCreateOpen] = useState(false)
    const [createdName, setCreatedName] = useState<string | null>(null)
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
                captureAndAlertRequestErrorHoc(deleteServiceAccountApi(row.id).then(() => load())).then((res) => {
                    if (res !== undefined) {
                        message({ title: t("prompt"), description: t("deleteSuccess"), variant: "success" })
                    }
                    next()
                })
            },
        })
    }

    return (
        <div className="flex h-full w-full flex-col gap-4 overflow-hidden p-4">
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

            <CreateServiceAccountDialog
                open={createOpen}
                onClose={setCreateOpen}
                onCreated={(name) => {
                    setCreatedName(name)
                    load(1, keyword)
                }}
            />

            {/* Post-create next step: issuing the first key lives on the detail
                page (follow-up task); until then the note is informational. */}
            <Dialog open={!!createdName} onOpenChange={(next) => !next && setCreatedName(null)}>
                <DialogContent className="sm:max-w-[480px]">
                    <DialogHeader>
                        <DialogTitle>{t("createdDialog.title")}</DialogTitle>
                    </DialogHeader>
                    <p className="text-sm text-muted-foreground">{t("createdDialog.nextStep", { name: createdName ?? "" })}</p>
                    <DialogFooter className="mt-4">
                        <Button className="h-10 w-[120px] px-16" onClick={() => setCreatedName(null)}>
                            {t("confirm.gotIt")}
                        </Button>
                    </DialogFooter>
                </DialogContent>
            </Dialog>
        </div>
    )
}
