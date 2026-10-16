import { bsConfirm } from "@/components/bs-ui/alertDialog/useConfirm"
import { Badge } from "@/components/bs-ui/badge"
import { Button } from "@/components/bs-ui/button"
import { Checkbox } from "@/components/bs-ui/checkBox"
import { Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle } from "@/components/bs-ui/dialog"
import { SearchInput } from "@/components/bs-ui/input"
import { Label } from "@/components/bs-ui/label"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/bs-ui/select"
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/bs-ui/table"
import { message } from "@/components/bs-ui/toast/use-toast"
import { getAppsApi } from "@/controllers/API/flow"
import { readFileLibDatabase } from "@/controllers/API/index"
import { listResourceGrantsApi, mutateResourceGrantsApi } from "@/controllers/API/serviceAccount"
import { captureAndAlertRequestErrorHoc } from "@/controllers/request"
import {
    ResourceGrant,
    ResourceGrantItemInput,
    ResourceGrantLevel,
    ResourceGrantRevokeInput,
    ResourceGrantType,
    ServiceAccount,
} from "@/types/api/serviceAccount"
import { useCallback, useEffect, useMemo, useRef, useState } from "react"
import { useTranslation } from "react-i18next"

/** Backend registry order (resource_grant_service.GRANTABLE_RESOURCE_TYPES). */
const RESOURCE_TYPES: ResourceGrantType[] = ["knowledge_library", "knowledge_space", "assistant", "workflow"]
const GRANT_LEVELS: ResourceGrantLevel[] = ["viewer", "editor", "manager"]

interface PickerItem {
    id: string
    name: string
}

/**
 * Where the grant dialog searches for candidates. The backend re-checks every id
 * (unsupported type / level → 400, no manager rights on the resource → 403), so
 * these lists only need to be convenient, not authoritative.
 */
const RESOURCE_LOADERS: Record<ResourceGrantType, (keyword: string, pageSize: number) => Promise<PickerItem[]>> = {
    // managed=true asks the backend for apps the caller may edit; admins bypass it.
    assistant: async (keyword, pageSize) =>
        (await getAppsApi({ type: "assistant", keyword, pageSize, managed: true })).data.map((one) => ({
            id: String(one.id),
            name: one.name,
        })),
    workflow: async (keyword, pageSize) =>
        (await getAppsApi({ type: "flow", keyword, pageSize, managed: true })).data.map((one) => ({
            id: String(one.id),
            name: one.name,
        })),
    knowledge_library: async (keyword, pageSize) => {
        // Document bases (type=0) and QA bases (type=1) are the same FGA plane; asking
        // for type=0 alone would make every QA base unofferable here.
        const [docs, qas] = await Promise.all([
            readFileLibDatabase({ name: keyword, type: 0, pageSize }),
            readFileLibDatabase({ name: keyword, type: 1, pageSize }),
        ])
        return [...docs.data, ...qas.data].map((one) => ({ id: String(one.id), name: one.name }))
    },
    // Spaces live in the same table (type=3).
    knowledge_space: async (keyword, pageSize) =>
        (await readFileLibDatabase({ name: keyword, type: 3, pageSize })).data.map((one) => ({
            id: String(one.id),
            name: one.name,
        })),
}
const PICKER_PAGE_SIZE = 50
/** Ids whose names are resolved eagerly for the table; beyond this they show as raw ids. */
const NAME_LOOKUP_PAGE_SIZE = 200

interface ResourceGrantDialogProps {
    open: boolean
    accountId: number
    onClose: (open: boolean) => void
    onGranted: () => void
}

function ResourceGrantDialog({ open, accountId, onClose, onGranted }: ResourceGrantDialogProps) {
    const { t } = useTranslation("serviceAccount")
    const [resourceType, setResourceType] = useState<ResourceGrantType>("knowledge_library")
    const [keyword, setKeyword] = useState("")
    const [options, setOptions] = useState<PickerItem[]>([])
    const [selected, setSelected] = useState<string[]>([])
    const [level, setLevel] = useState<ResourceGrantLevel>("viewer")
    const [loading, setLoading] = useState(false)
    const [submitting, setSubmitting] = useState(false)

    // Guards against a slow response from the previous type landing last.
    const searchSeq = useRef(0)
    const search = useCallback(async (type: ResourceGrantType, name: string) => {
        const seq = ++searchSeq.current
        setLoading(true)
        const res = await captureAndAlertRequestErrorHoc(RESOURCE_LOADERS[type](name, PICKER_PAGE_SIZE))
        if (seq !== searchSeq.current) return
        setOptions(res || [])
        setLoading(false)
    }, [])

    // Fresh form on every open; re-search whenever the type changes.
    useEffect(() => {
        if (!open) return
        setKeyword("")
        setSelected([])
        setLevel("viewer")
        void search(resourceType, "")
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [open])

    const handleTypeChange = (next: ResourceGrantType) => {
        setResourceType(next)
        setSelected([])
        // Clear first: while the new type loads, the previous type's rows must not be
        // clickable (they would be submitted under the new resource type).
        setOptions([])
        void search(next, keyword)
    }

    const handleToggle = (id: string) => {
        setSelected((prev) => (prev.includes(id) ? prev.filter((one) => one !== id) : [...prev, id]))
    }

    const handleConfirm = () => {
        if (!selected.length) {
            message({ title: t("prompt"), description: t("grants.selectRequired"), variant: "warning" })
            return
        }
        const grants: ResourceGrantItemInput[] = selected.map((id) => ({
            resource_type: resourceType,
            resource_id: id,
            level,
        }))
        setSubmitting(true)
        captureAndAlertRequestErrorHoc(mutateResourceGrantsApi(accountId, { grants }))
            .then((res) => {
                if (!res) return
                message({
                    title: t("prompt"),
                    description: t("grants.grantSuccess", { count: res.granted }),
                    variant: "success",
                })
                onClose(false)
                onGranted()
            })
            .finally(() => setSubmitting(false))
    }

    return (
        <Dialog
            open={open}
            onOpenChange={(next) => {
                if (next) return
                // The mutate is already on the wire; letting the form vanish now would
                // leave the admin believing a submitted grant was cancelled.
                if (submitting) return
                onClose(false)
            }}
        >
            <DialogContent className="sm:max-w-[640px]" close={!submitting}>
                <DialogHeader>
                    <DialogTitle>{t("grants.dialog.title")}</DialogTitle>
                </DialogHeader>
                <div className="flex flex-col gap-5">
                    <div className="flex items-end gap-4">
                        <div className="w-[220px]">
                            <Label className="bisheng-label">{t("grants.dialog.type")}</Label>
                            <Select value={resourceType} onValueChange={(v) => handleTypeChange(v as ResourceGrantType)}>
                                <SelectTrigger>
                                    <SelectValue />
                                </SelectTrigger>
                                <SelectContent>
                                    {RESOURCE_TYPES.map((type) => (
                                        <SelectItem key={type} value={type}>
                                            {t(`grants.type.${type}`)}
                                        </SelectItem>
                                    ))}
                                </SelectContent>
                            </Select>
                        </div>
                        <div className="w-[220px]">
                            <Label className="bisheng-label">{t("grants.dialog.level")}</Label>
                            <Select value={level} onValueChange={(v) => setLevel(v as ResourceGrantLevel)}>
                                <SelectTrigger>
                                    <SelectValue />
                                </SelectTrigger>
                                <SelectContent>
                                    {GRANT_LEVELS.map((one) => (
                                        <SelectItem key={one} value={one}>
                                            {t(`grants.level.${one}`)}
                                        </SelectItem>
                                    ))}
                                </SelectContent>
                            </Select>
                        </div>
                    </div>
                    {/* One level for the whole batch: the mutate endpoint takes it per
                        item, and "these N resources at this level" is the only shape
                        that stays comprehensible in one dialog. */}
                    <p className="text-xs text-muted-foreground">{t(`grants.levelHint.${level}`)}</p>

                    <div className="flex flex-col gap-2">
                        <Label className="bisheng-label">{t("grants.dialog.resource")}</Label>
                        <SearchInput
                            placeholder={t("grants.dialog.searchPlaceholder")}
                            value={keyword}
                            onChange={(e) => setKeyword(e.target.value)}
                            onKeyDown={(e) => {
                                if (e.key === "Enter") void search(resourceType, keyword)
                            }}
                        />
                        <div className="max-h-[260px] min-h-[120px] overflow-y-auto rounded-md border p-2">
                            {loading ? (
                                <p className="p-3 text-center text-sm text-muted-foreground">{t("loading")}</p>
                            ) : options.length === 0 ? (
                                <p className="p-3 text-center text-sm text-muted-foreground">{t("grants.dialog.empty")}</p>
                            ) : (
                                options.map((option) => (
                                    <label
                                        key={option.id}
                                        className="flex cursor-pointer items-center gap-2 rounded px-2 py-1.5 text-sm hover:bg-accent"
                                    >
                                        <Checkbox
                                            checked={selected.includes(option.id)}
                                            onCheckedChange={() => handleToggle(option.id)}
                                        />
                                        <span className="min-w-0 flex-1 truncate">{option.name}</span>
                                        <code className="shrink-0 text-xs text-muted-foreground">{option.id}</code>
                                    </label>
                                ))
                            )}
                        </div>
                        <p className="text-xs text-muted-foreground">
                            {t("grants.dialog.selected", { count: selected.length })}
                        </p>
                        <p className="text-xs text-muted-foreground">{t("grants.dialog.adminOnlyHint")}</p>
                    </div>
                </div>
                <DialogFooter className="mt-4">
                    <Button
                        variant="outline"
                        className="h-10 w-[120px] px-16"
                        disabled={submitting}
                        onClick={() => onClose(false)}
                    >
                        {t("cancel")}
                    </Button>
                    <Button className="h-10 w-[120px] px-16" disabled={submitting} onClick={handleConfirm}>
                        {t("ok")}
                    </Button>
                </DialogFooter>
            </DialogContent>
        </Dialog>
    )
}

interface ResourceGrantsTabProps {
    account: ServiceAccount
    /** Grants change what the account can reach; the parent refreshes the account. */
    onGrantsChanged: () => void
}

export function ResourceGrantsTab({ account, onGrantsChanged }: ResourceGrantsTabProps) {
    const { t } = useTranslation("serviceAccount")
    const [rows, setRows] = useState<ResourceGrant[]>([])
    const [names, setNames] = useState<Record<string, string>>({})
    const [selected, setSelected] = useState<string[]>([])
    const [loading, setLoading] = useState(true)
    const [dialogOpen, setDialogOpen] = useState(false)

    // seq: an earlier, slower listing must not overwrite a newer one.
    const loadSeq = useRef(0)
    const [loadFailed, setLoadFailed] = useState(false)
    const load = useCallback(async () => {
        const seq = ++loadSeq.current
        const res = await captureAndAlertRequestErrorHoc(listResourceGrantsApi(account.id))
        if (seq !== loadSeq.current) return
        if (res) {
            const next: ResourceGrant[] = res.data || []
            setRows(next)
            setLoadFailed(false)
            // Drop selections that no longer exist or are no longer revocable here.
            setSelected((prev) =>
                prev.filter((key) =>
                    next.some((row) => key === `${row.resource_type}:${row.resource_id}` && row.source === "direct"),
                ),
            )
        } else {
            // A failed listing must not masquerade as "this account has no grants".
            setLoadFailed(true)
        }
        setLoading(false)
    }, [account.id])

    useEffect(() => {
        void load()
    }, [load])

    const rowKey = (row: ResourceGrant) => `${row.resource_type}:${row.resource_id}`

    // Resolve ids to names through the same list endpoints the dialog searches;
    // an id that is no longer visible simply keeps showing its raw id.
    const typeKey = useMemo(
        () => [...new Set(rows.map((row) => row.resource_type))].sort().join(","),
        [rows],
    )
    useEffect(() => {
        if (!typeKey) return
        const types = typeKey.split(",") as ResourceGrantType[]
        void (async () => {
            const entries = await Promise.all(
                types.map(async (type) => {
                    const res = await captureAndAlertRequestErrorHoc(RESOURCE_LOADERS[type]("", NAME_LOOKUP_PAGE_SIZE))
                    return (res || []).map((item) => [`${type}:${item.id}`, item.name] as const)
                }),
            )
            // Merge: replacing would wipe the names of every other type whenever one
            // type's lookup fails.
            setNames((prev) => ({ ...prev, ...Object.fromEntries(entries.flat()) }))
        })()
    }, [typeKey])

    const directRows = rows.filter((row) => row.source === "direct")
    const selectedDirect = selected.filter((key) => directRows.some((row) => rowKey(row) === key))

    const handleRevoke = (row: ResourceGrant) => {
        const revoke: ResourceGrantRevokeInput = {
            resource_type: row.resource_type,
            resource_id: row.resource_id,
        }
        bsConfirm({
            title: t("prompt"),
            desc: t("grants.confirm.revokeDesc", { name: names[rowKey(row)] || row.resource_id }),
            okTxt: t("grants.revoke"),
            onOk(next) {
                captureAndAlertRequestErrorHoc(mutateResourceGrantsApi(account.id, { revokes: [revoke] })).then(
                    (res) => {
                        if (res) {
                            message({
                                title: t("prompt"),
                                description: t("grants.revokeSuccess", { count: res.revoked }),
                                variant: "success",
                            })
                            void load()
                            onGrantsChanged()
                        }
                        next()
                    },
                )
            },
        })
    }

    const handleRevokeSelected = () => {
        const revokes: ResourceGrantRevokeInput[] = directRows
            .filter((row) => selected.includes(rowKey(row)))
            .map((row) => ({ resource_type: row.resource_type, resource_id: row.resource_id }))
        if (!revokes.length) return
        bsConfirm({
            title: t("prompt"),
            desc: t("grants.confirm.revokeSelectedDesc", { count: revokes.length }),
            okTxt: t("grants.revoke"),
            onOk(next) {
                captureAndAlertRequestErrorHoc(mutateResourceGrantsApi(account.id, { revokes })).then((res) => {
                    if (res) {
                        message({
                            title: t("prompt"),
                            description: t("grants.revokeSuccess", { count: res.revoked }),
                            variant: "success",
                        })
                        setSelected([])
                        void load()
                        onGrantsChanged()
                    }
                    next()
                })
            },
        })
    }

    return (
        <div className="flex h-full w-full flex-col gap-3 overflow-hidden">
            <div className="flex shrink-0 flex-wrap items-center justify-between gap-3">
                <p className="text-sm text-muted-foreground">{t("grants.hint")}</p>
                <div className="flex items-center gap-2">
                    {selectedDirect.length > 0 && (
                        <Button
                            variant="outline"
                            className="px-6 text-red-500"
                            onClick={handleRevokeSelected}
                        >
                            {t("grants.revokeSelected", { count: selectedDirect.length })}
                        </Button>
                    )}
                    <Button className="px-6" onClick={() => setDialogOpen(true)}>
                        {t("grants.add")}
                    </Button>
                </div>
            </div>

            <div className="min-h-0 flex-1 overflow-y-auto rounded-lg border">
                <Table>
                    <TableHeader>
                        <TableRow>
                            <TableHead className="w-[44px]">
                                <Checkbox
                                    checked={directRows.length > 0 && selectedDirect.length === directRows.length}
                                    onCheckedChange={() =>
                                        setSelected(selectedDirect.length === directRows.length ? [] : directRows.map(rowKey))
                                    }
                                />
                            </TableHead>
                            <TableHead className="w-[150px]">{t("grants.columns.type")}</TableHead>
                            <TableHead>{t("grants.columns.name")}</TableHead>
                            <TableHead className="w-[120px]">{t("grants.columns.level")}</TableHead>
                            <TableHead className="w-[170px]">{t("grants.columns.source")}</TableHead>
                            <TableHead className="w-[110px] text-right">{t("columns.actions")}</TableHead>
                        </TableRow>
                    </TableHeader>
                    <TableBody>
                        {loading && rows.length === 0 ? (
                            <TableRow>
                                <TableCell colSpan={6} className="text-center text-muted-foreground">
                                    {t("loading")}
                                </TableCell>
                            </TableRow>
                        ) : loadFailed ? (
                            <TableRow>
                                <TableCell colSpan={6} className="text-center text-muted-foreground">
                                    {t("grants.loadFailed")}
                                    <Button variant="link" size="sm" onClick={() => void load()}>
                                        {t("grants.retry")}
                                    </Button>
                                </TableCell>
                            </TableRow>
                        ) : rows.length === 0 ? (
                            <TableRow>
                                <TableCell colSpan={6} className="text-center text-muted-foreground">
                                    {t("grants.emptyHint")}
                                </TableCell>
                            </TableRow>
                        ) : (
                            rows.map((row) => {
                                const key = rowKey(row)
                                const direct = row.source === "direct"
                                return (
                                    <TableRow key={key}>
                                        <TableCell>
                                            <Checkbox
                                                checked={selected.includes(key)}
                                                // Inherited visibility has no tuple to revoke.
                                                disabled={!direct}
                                                onCheckedChange={() =>
                                                    setSelected((prev) =>
                                                        prev.includes(key)
                                                            ? prev.filter((one) => one !== key)
                                                            : [...prev, key],
                                                    )
                                                }
                                            />
                                        </TableCell>
                                        <TableCell>
                                            <Badge variant="secondary">{t(`grants.type.${row.resource_type}`)}</Badge>
                                        </TableCell>
                                        <TableCell>
                                            {names[key] ? (
                                                <span>{names[key]}</span>
                                            ) : (
                                                <code className="text-sm text-muted-foreground">{row.resource_id}</code>
                                            )}
                                        </TableCell>
                                        <TableCell>
                                            {row.level ? t(`grants.level.${row.level}`) : t("grants.levelNone")}
                                        </TableCell>
                                        <TableCell>
                                            <div className="flex flex-col gap-1">
                                                <Badge variant={direct ? "success" : "gray"}>
                                                    {t(`grants.source.${row.source}`)}
                                                </Badge>
                                                {!direct && (
                                                    <span className="text-xs text-muted-foreground">
                                                        {t("grants.inheritedHint")}
                                                    </span>
                                                )}
                                            </div>
                                        </TableCell>
                                        <TableCell className="text-right">
                                            <Button
                                                variant="link"
                                                size="sm"
                                                className="text-red-500"
                                                disabled={!direct}
                                                onClick={() => handleRevoke(row)}
                                            >
                                                {t("grants.revoke")}
                                            </Button>
                                        </TableCell>
                                    </TableRow>
                                )
                            })
                        )}
                    </TableBody>
                </Table>
            </div>

            <ResourceGrantDialog
                open={dialogOpen}
                accountId={account.id}
                onClose={setDialogOpen}
                onGranted={() => {
                    void load()
                    onGrantsChanged()
                }}
            />
        </div>
    )
}
