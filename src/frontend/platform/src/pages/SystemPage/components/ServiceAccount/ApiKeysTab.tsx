import { bsConfirm } from "@/components/bs-ui/alertDialog/useConfirm"
import { Badge } from "@/components/bs-ui/badge"
import { Button } from "@/components/bs-ui/button"
import { SearchInput } from "@/components/bs-ui/input"
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/bs-ui/table"
import { message } from "@/components/bs-ui/toast/use-toast"
import {
    listAssignableAgentsApi,
    listOpenApiScopesApi,
    listServiceAccountKeysApi,
    revealServiceAccountKeyApi,
    revokeAllServiceAccountKeysApi,
    revokeServiceAccountKeyApi,
} from "@/controllers/API/serviceAccount"
import { captureAndAlertRequestErrorHoc } from "@/controllers/request"
import {
    AssignableAgent,
    OpenApiScope,
    ServiceAccount,
    ServiceAccountKey,
    ServiceAccountKeyIssued,
} from "@/types/api/serviceAccount"
import { formatIsoDateTime } from "@/util/utils"
import { useCallback, useEffect, useMemo, useState } from "react"
import { useTranslation } from "react-i18next"
import { KeyIssueDialog, scopeLocaleKey } from "./KeyIssueDialog"
import { KeyRevealDialog } from "./KeyRevealDialog"

const STATUS_VARIANT: Record<ServiceAccountKey["status"], "success" | "error" | "gray"> = {
    active: "success",
    expired: "error",
    revoked: "gray",
}

interface ApiKeysTabProps {
    account: ServiceAccount
    /** Every key write changes key_count / last_used_at on the account, so the
     *  parent re-reads it instead of trusting the write responses. */
    onKeysChanged: () => void
    /** Switches the detail view to the grants tab (setup order: grant, then issue). */
    onGoToGrants?: () => void
}

export function ApiKeysTab({ account, onKeysChanged, onGoToGrants }: ApiKeysTabProps) {
    const { t } = useTranslation("serviceAccount")
    const [keys, setKeys] = useState<ServiceAccountKey[]>([])
    const [scopes, setScopes] = useState<OpenApiScope[]>([])
    const [assignableAgents, setAssignableAgents] = useState<AssignableAgent[]>([])
    const [query, setQuery] = useState("")
    const [loading, setLoading] = useState(true)
    const [issueOpen, setIssueOpen] = useState(false)
    const [editing, setEditing] = useState<ServiceAccountKey | null>(null)
    const [revealed, setRevealed] = useState<{
        name: string | null
        plaintext: string
        mode: "issued" | "viewed"
    } | null>(null)

    const load = useCallback(async () => {
        const res = await captureAndAlertRequestErrorHoc(listServiceAccountKeysApi(account.id))
        if (res) setKeys(res.data || [])
        setLoading(false)
    }, [account.id])

    useEffect(() => {
        void load()
    }, [load])

    // Scope catalog for the issue dialog: a key may only carry registered scopes.
    useEffect(() => {
        void captureAndAlertRequestErrorHoc(listOpenApiScopesApi()).then((res) => {
            if (res) setScopes(res.data || [])
        })
    }, [])

    // Agents the account can be restricted to; also resolves names in the table.
    const loadAssignableAgents = useCallback(async () => {
        const res = await captureAndAlertRequestErrorHoc(listAssignableAgentsApi(account.id))
        if (res) setAssignableAgents(res.data || [])
    }, [account.id])

    useEffect(() => {
        void loadAssignableAgents()
    }, [loadAssignableAgents])

    // "Revoke all" clears every key that is not revoked yet -- expired ones
    // included (the backend filters on revoked_at alone), so the count and the
    // button state must use the same definition or the dialog would under-report
    // and the cleanup would be unreachable while only expired keys remain.
    const unrevokedCount = keys.filter((row) => row.status !== "revoked").length

    // Resolve a stored allowlist id to its display name; stale ids keep the raw id.
    const agentLabel = useCallback(
        (ref: { type: string; id: string }) => {
            const found = assignableAgents.find((agent) => agent.type === ref.type && agent.id === ref.id)
            return found ? `${t(`keys.agents.type.${ref.type}`)} · ${found.name}` : ref.id
        },
        [assignableAgents, t],
    )

    // Client-side filter: the key list endpoint returns every key (no pagination).
    // Match the text the admin can see, including scope/agent names and the mask.
    const visibleKeys = useMemo(() => {
        const needle = query.trim().toLowerCase()
        if (!needle) return keys
        return keys.filter((row) => {
            const scopes = row.scopes
                .map((code) => `${code} ${t(scopeLocaleKey(code), { defaultValue: code })}`)
                .join(" ")
            const agents =
                row.allowed_apps === null
                    ? t("keys.agents.unrestricted")
                    : row.allowed_apps.length === 0
                      ? t("keys.agents.none")
                      : row.allowed_apps.map((ref) => `${agentLabel(ref)} ${ref.id}`).join(" ")
            const haystack = [
                row.name ?? t("keys.unnamed"),
                row.key_mask,
                String(row.id),
                t(`keys.status.${row.status}`),
                scopes,
                agents,
                row.expires_at ? formatIsoDateTime(row.expires_at) : "",
                row.last_used_at ? formatIsoDateTime(row.last_used_at) : "",
                row.revoked_at ? formatIsoDateTime(row.revoked_at) : "",
            ]
                .join(" ")
                .toLowerCase()
            return haystack.includes(needle)
        })
    }, [keys, query, agentLabel, t])

    const openIssue = () => {
        setEditing(null)
        setIssueOpen(true)
    }

    const openEdit = (row: ServiceAccountKey) => {
        setEditing(row)
        setIssueOpen(true)
    }

    const handleIssued = (issued: ServiceAccountKeyIssued, name: string) => {
        void load()
        onKeysChanged()
        if (!issued?.plaintext) {
            // Contract violation: the key exists but no plaintext came back, so it
            // can never actually be used. Say so instead of opening an empty dialog.
            message({ title: t("prompt"), description: t("keys.plaintextMissing"), variant: "error" })
            return
        }
        // The plaintext lives only in this dialog's props from here on; the list
        // below is refetched from an endpoint that returns masks.
        setRevealed({ name, plaintext: issued.plaintext, mode: "issued" })
    }

    const handleReveal = (row: ServiceAccountKey) => {
        captureAndAlertRequestErrorHoc(revealServiceAccountKeyApi(account.id, row.id)).then((res) => {
            if (!res?.plaintext) return
            setRevealed({ name: row.name, plaintext: res.plaintext, mode: "viewed" })
        })
    }

    const handleRevoke = (row: ServiceAccountKey) => {
        bsConfirm({
            title: t("prompt"),
            desc: t("keys.confirm.revokeDesc", { name: row.name || t("keys.unnamed") }),
            okTxt: t("keys.revoke"),
            onOk(next) {
                captureAndAlertRequestErrorHoc(revokeServiceAccountKeyApi(account.id, row.id)).then((res) => {
                    if (res) {
                        message({ title: t("prompt"), description: t("keys.revokeSuccess"), variant: "success" })
                        void load()
                        onKeysChanged()
                    }
                    next()
                })
            },
        })
    }

    const handleRevokeAll = () => {
        bsConfirm({
            title: t("prompt"),
            desc: t("keys.confirm.revokeAllDesc", { count: unrevokedCount }),
            okTxt: t("keys.revokeAll"),
            onOk(next) {
                captureAndAlertRequestErrorHoc(revokeAllServiceAccountKeysApi(account.id)).then((res) => {
                    if (res) {
                        message({
                            title: t("prompt"),
                            description: t("keys.revokeAllSuccess", { count: res.revoked }),
                            variant: "success",
                        })
                        void load()
                        onKeysChanged()
                    }
                    next()
                })
            },
        })
    }

    return (
        <div className="flex h-full w-full flex-col gap-3 overflow-hidden">
            <div className="flex shrink-0 flex-wrap items-center justify-between gap-3">
                <p className="text-sm text-muted-foreground">{t("keys.oneTimeNotice")}</p>
                <div className="flex flex-wrap items-center gap-2">
                    <SearchInput
                        className="w-[240px]"
                        placeholder={t("keys.searchPlaceholder")}
                        value={query}
                        onChange={(e) => setQuery(e.target.value)}
                    />
                    {!account.enabled && <span className="text-sm text-red-500">{t("keys.accountDisabledHint")}</span>}
                    <Button variant="outline" className="px-6" disabled={!unrevokedCount} onClick={handleRevokeAll}>
                        {t("keys.revokeAll")}
                    </Button>
                    {/* Issuing a key on a disabled account answers HTTP 401 (11805),
                        and the request wrapper treats any 401 as an expired session:
                        it clears the token and redirects to the login page. The
                        button therefore has to be impossible to press here. */}
                    <Button className="px-6" disabled={!account.enabled} onClick={openIssue}>
                        {t("keys.issue")}
                    </Button>
                </div>
            </div>

            <div className="min-h-0 flex-1 overflow-y-auto rounded-lg border">
                <Table>
                    <TableHeader>
                        <TableRow>
                            <TableHead>{t("keys.columns.name")}</TableHead>
                            <TableHead>{t("keys.columns.key")}</TableHead>
                            <TableHead>{t("keys.columns.scopes")}</TableHead>
                            <TableHead>{t("keys.columns.agents")}</TableHead>
                            <TableHead className="w-[140px]">{t("keys.columns.status")}</TableHead>
                            <TableHead className="w-[190px]">{t("keys.columns.expires")}</TableHead>
                            <TableHead className="w-[170px]">{t("keys.columns.lastUsed")}</TableHead>
                            <TableHead className="w-[220px] text-right">{t("columns.actions")}</TableHead>
                        </TableRow>
                    </TableHeader>
                    <TableBody>
                        {loading && keys.length === 0 ? (
                            <TableRow>
                                <TableCell colSpan={8} className="text-center text-muted-foreground">
                                    {t("loading")}
                                </TableCell>
                            </TableRow>
                        ) : keys.length === 0 ? (
                            <TableRow>
                                <TableCell colSpan={8} className="text-center text-muted-foreground">
                                    {/* Empty state doubles as the setup entry point: a
                                        restricted key can only target agents the account
                                        has already been granted. */}
                                    {t("keys.emptyHint")}
                                    {onGoToGrants && (
                                        <Button variant="link" size="sm" onClick={onGoToGrants}>
                                            {t("keys.form.goToGrants")}
                                        </Button>
                                    )}
                                </TableCell>
                            </TableRow>
                        ) : visibleKeys.length === 0 ? (
                            <TableRow>
                                <TableCell colSpan={8} className="text-center text-muted-foreground">
                                    {t("keys.noMatch")}
                                    <Button variant="link" size="sm" onClick={() => setQuery("")}>
                                        {t("keys.clearSearch")}
                                    </Button>
                                </TableCell>
                            </TableRow>
                        ) : (
                            visibleKeys.map((row) => (
                                <TableRow key={row.id}>
                                    <TableCell>{row.name || t("keys.unnamed")}</TableCell>
                                    <TableCell>
                                        {/* Mask only; plaintext is available through the audited reveal action. */}
                                        <code className="font-mono text-sm">{row.key_mask}</code>
                                    </TableCell>
                                    <TableCell>
                                        <div className="flex flex-wrap gap-1">
                                            {row.scopes.length === 0 ? (
                                                <span className="text-muted-foreground">{t("keys.noScopes")}</span>
                                            ) : (
                                                row.scopes.map((code) => (
                                                    <Badge key={code} variant="secondary" title={code}>
                                                        {t(scopeLocaleKey(code), { defaultValue: code })}
                                                    </Badge>
                                                ))
                                            )}
                                        </div>
                                    </TableCell>
                                    <TableCell>
                                        <div className="flex flex-wrap gap-1">
                                            {row.allowed_apps === null ? (
                                                <span className="text-muted-foreground">
                                                    {t("keys.agents.unrestricted")}
                                                </span>
                                            ) : row.allowed_apps.length === 0 ? (
                                                <span className="text-muted-foreground">{t("keys.agents.none")}</span>
                                            ) : (
                                                row.allowed_apps.map((ref) => (
                                                    <Badge
                                                        key={`${ref.type}:${ref.id}`}
                                                        variant="secondary"
                                                        title={agentLabel(ref)}
                                                    >
                                                        {agentLabel(ref)}
                                                    </Badge>
                                                ))
                                            )}
                                        </div>
                                    </TableCell>
                                    <TableCell>
                                        <div className="flex flex-col gap-1">
                                            <Badge variant={STATUS_VARIANT[row.status]}>
                                                {t(`keys.status.${row.status}`)}
                                            </Badge>
                                            {row.revoked_at && (
                                                <span className="text-xs text-muted-foreground">
                                                    {t("keys.revokedAt", { time: formatIsoDateTime(row.revoked_at) })}
                                                </span>
                                            )}
                                        </div>
                                    </TableCell>
                                    <TableCell className="text-muted-foreground">
                                        {row.expires_at ? formatIsoDateTime(row.expires_at) : t("keys.neverExpires")}
                                    </TableCell>
                                    <TableCell className="text-muted-foreground">
                                        {row.last_used_at ? formatIsoDateTime(row.last_used_at) : t("never")}
                                    </TableCell>
                                    <TableCell className="text-right">
                                        {row.revealable ? (
                                            <Button variant="link" size="sm" onClick={() => handleReveal(row)}>
                                                {t("keys.reveal.action")}
                                            </Button>
                                        ) : (
                                            // Legacy keys have no ciphertext; a disabled button's
                                            // title is unreachable (pointer-events: none), so show a
                                            // non-interactive hint that can actually explain why.
                                            <span
                                                className="cursor-help text-xs text-muted-foreground"
                                                title={t("keys.reveal.notRevealableHint")}
                                            >
                                                {t("keys.reveal.notRevealable")}
                                            </span>
                                        )}
                                        <Button
                                            variant="link"
                                            size="sm"
                                            disabled={row.status === "revoked"}
                                            onClick={() => openEdit(row)}
                                        >
                                            {t("keys.edit")}
                                        </Button>
                                        <Button
                                            variant="link"
                                            size="sm"
                                            className="text-red-500"
                                            disabled={row.status === "revoked"}
                                            onClick={() => handleRevoke(row)}
                                        >
                                            {t("keys.revoke")}
                                        </Button>
                                    </TableCell>
                                </TableRow>
                            ))
                        )}
                    </TableBody>
                </Table>
            </div>

            <KeyIssueDialog
                open={issueOpen}
                accountId={account.id}
                scopes={scopes}
                assignableAgents={assignableAgents}
                editing={editing}
                onClose={setIssueOpen}
                onGoToGrants={onGoToGrants}
                onIssued={handleIssued}
                onUpdated={() => {
                    void load()
                    void loadAssignableAgents()
                    onKeysChanged()
                }}
            />

            <KeyRevealDialog
                open={!!revealed}
                keyName={revealed?.name ?? null}
                plaintext={revealed?.plaintext ?? ""}
                mode={revealed?.mode ?? "issued"}
                onClose={() => setRevealed(null)}
            />
        </div>
    )
}
