import { bsConfirm } from "@/components/bs-ui/alertDialog/useConfirm"
import { Badge } from "@/components/bs-ui/badge"
import { Button } from "@/components/bs-ui/button"
import { Checkbox } from "@/components/bs-ui/checkBox"
import { SearchInput } from "@/components/bs-ui/input"
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/bs-ui/table"
import { message } from "@/components/bs-ui/toast/use-toast"
import { listResourceGrantsApi, mutateResourceGrantsApi } from "@/controllers/API/serviceAccount"
import { captureAndAlertRequestErrorHoc } from "@/controllers/request"
import { ResourceGrant, ResourceGrantRevokeInput, ResourceGrantType, ServiceAccount } from "@/types/api/serviceAccount"
import { ArrowUpDown, ChevronDown, ChevronUp } from "lucide-react"
import { useCallback, useEffect, useMemo, useRef, useState } from "react"
import { useTranslation } from "react-i18next"
import { ResourceGrantDialog } from "./ResourceGrantDialog"
import { ResourceGrantLevelDialog } from "./ResourceGrantLevelDialog"
import { GRANT_LEVELS, NAME_LOOKUP_PAGE_SIZE, RESOURCE_LOADERS, RESOURCE_TYPES } from "./resourceGrantLoaders"

type SortField = "type" | "name" | "level" | "source"
interface SortState {
    field: SortField
    dir: "asc" | "desc"
}

/** null (non-delegable / inherited) sorts last regardless of direction. */
const levelRank = (level: ResourceGrant["level"]) => (level === null ? 999 : GRANT_LEVELS.indexOf(level))

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
    const [query, setQuery] = useState("")
    const [sort, setSort] = useState<SortState | null>(null)
    const [editingGrant, setEditingGrant] = useState<ResourceGrant | null>(null)

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

    const rowKey = useCallback((row: ResourceGrant) => `${row.resource_type}:${row.resource_id}`, [])
    const displayName = useCallback(
        (row: ResourceGrant) => names[rowKey(row)] ?? row.resource_id,
        [names, rowKey],
    )

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

    // Client-side filter + sort: the grants endpoint returns the whole list and is
    // not paginated. Filter on exactly what the user sees (resolved name or raw id)
    // so rows whose names are outside the first lookup page remain searchable.
    const visibleRows = useMemo(() => {
        const needle = query.trim().toLowerCase()
        const filtered = needle
            ? rows.filter((row) =>
                  [
                      displayName(row),
                      row.resource_id,
                      t(`grants.type.${row.resource_type}`),
                      row.level ? t(`grants.level.${row.level}`) : "",
                      t(`grants.source.${row.source}`),
                  ]
                      .join(" ")
                      .toLowerCase()
                      .includes(needle),
              )
            : rows
        if (!sort) return filtered

        const compare = (left: ResourceGrant, right: ResourceGrant): number => {
            switch (sort.field) {
                case "type":
                    return RESOURCE_TYPES.indexOf(left.resource_type) - RESOURCE_TYPES.indexOf(right.resource_type)
                case "name":
                    return displayName(left).localeCompare(displayName(right), undefined, {
                        numeric: true,
                        sensitivity: "base",
                    })
                case "level":
                    return levelRank(left.level) - levelRank(right.level)
                case "source":
                    return left.source === right.source ? 0 : left.source === "direct" ? -1 : 1
            }
        }

        return [...filtered].sort((left, right) => {
            // Keep null levels last in both directions: the em dash is not an
            // ordering value the admin asked to sort on.
            if (sort.field === "level" && (left.level === null || right.level === null)) {
                if (left.level === null && right.level === null) {
                    return rowKey(left).localeCompare(rowKey(right))
                }
                return left.level === null ? 1 : -1
            }
            const base = compare(left, right)
            const directed = sort.dir === "asc" ? base : -base
            return directed || rowKey(left).localeCompare(rowKey(right))
        })
    }, [rows, query, sort, t, displayName, rowKey])

    const toggleSort = (field: SortField) => {
        setSort((prev) => {
            if (!prev || prev.field !== field) return { field, dir: "asc" }
            if (prev.dir === "asc") return { field, dir: "desc" }
            return null
        })
    }

    const sortIcon = (field: SortField) => {
        if (sort?.field !== field) return <ArrowUpDown className="h-3.5 w-3.5 text-muted-foreground" />
        return sort.dir === "asc" ? <ChevronUp className="h-3.5 w-3.5" /> : <ChevronDown className="h-3.5 w-3.5" />
    }
    const sortButton = (field: SortField, label: string) => (
        <button type="button" className="inline-flex items-center gap-1" onClick={() => toggleSort(field)}>
            {label}
            {sortIcon(field)}
        </button>
    )
    const ariaSort = (field: SortField): "ascending" | "descending" | "none" => {
        if (sort?.field !== field) return "none"
        return sort.dir === "asc" ? "ascending" : "descending"
    }

    const directRows = rows.filter((row) => row.source === "direct")
    const selectedDirect = selected.filter((key) => directRows.some((row) => rowKey(row) === key))
    const visibleDirectRows = visibleRows.filter((row) => row.source === "direct")
    const allVisibleSelected =
        visibleDirectRows.length > 0 && visibleDirectRows.every((row) => selected.includes(rowKey(row)))

    const toggleVisibleSelection = () => {
        if (!visibleDirectRows.length) return
        if (allVisibleSelected) {
            const visibleKeys = new Set(visibleDirectRows.map(rowKey))
            setSelected((prev) => prev.filter((key) => !visibleKeys.has(key)))
            return
        }
        setSelected((prev) => [...prev, ...visibleDirectRows.map(rowKey).filter((key) => !prev.includes(key))])
    }

    const handleRevoke = (row: ResourceGrant) => {
        const revoke: ResourceGrantRevokeInput = {
            resource_type: row.resource_type,
            resource_id: row.resource_id,
        }
        bsConfirm({
            title: t("prompt"),
            desc: t("grants.confirm.revokeDesc", { name: displayName(row) }),
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
                <div className="flex flex-wrap items-center gap-2">
                    <SearchInput
                        className="w-[240px]"
                        placeholder={t("grants.searchPlaceholder")}
                        value={query}
                        onChange={(e) => setQuery(e.target.value)}
                    />
                    {selectedDirect.length > 0 && (
                        <Button
                            variant="outline"
                            className="px-6 text-red-500"
                            title={t("grants.revokeSelectedHint")}
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
                                    checked={allVisibleSelected}
                                    disabled={visibleDirectRows.length === 0}
                                    aria-label={t("grants.selectVisible")}
                                    onCheckedChange={toggleVisibleSelection}
                                />
                            </TableHead>
                            <TableHead className="w-[150px]" aria-sort={ariaSort("type")}>
                                {sortButton("type", t("grants.columns.type"))}
                            </TableHead>
                            <TableHead aria-sort={ariaSort("name")}>
                                {sortButton("name", t("grants.columns.name"))}
                            </TableHead>
                            <TableHead className="w-[120px]" aria-sort={ariaSort("level")}>
                                {sortButton("level", t("grants.columns.level"))}
                            </TableHead>
                            <TableHead className="w-[170px]" aria-sort={ariaSort("source")}>
                                {sortButton("source", t("grants.columns.source"))}
                            </TableHead>
                            <TableHead className="w-[170px] text-right">{t("columns.actions")}</TableHead>
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
                        ) : visibleRows.length === 0 ? (
                            <TableRow>
                                <TableCell colSpan={6} className="text-center text-muted-foreground">
                                    {t("grants.noMatch")}
                                    <Button variant="link" size="sm" onClick={() => setQuery("")}>
                                        {t("grants.clearSearch")}
                                    </Button>
                                </TableCell>
                            </TableRow>
                        ) : (
                            visibleRows.map((row) => {
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
                                            {direct && row.level && (
                                                <Button
                                                    variant="link"
                                                    size="sm"
                                                    disabled={!direct}
                                                    onClick={() => setEditingGrant(row)}
                                                >
                                                    {t("grants.edit")}
                                                </Button>
                                            )}
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

            <ResourceGrantLevelDialog
                open={!!editingGrant}
                accountId={account.id}
                grant={editingGrant}
                displayName={editingGrant ? displayName(editingGrant) : ""}
                onClose={(open) => {
                    if (!open) setEditingGrant(null)
                }}
                onUpdated={() => {
                    void load()
                    onGrantsChanged()
                }}
            />
        </div>
    )
}
