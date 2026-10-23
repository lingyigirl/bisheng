import { Button } from "@/components/bs-ui/button"
import { DatePicker } from "@/components/bs-ui/calendar/datePicker"
import { Checkbox } from "@/components/bs-ui/checkBox"
import { Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle } from "@/components/bs-ui/dialog"
import { Input } from "@/components/bs-ui/input"
import { Label } from "@/components/bs-ui/label"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/bs-ui/select"
import MultiSelect from "@/components/bs-ui/select/multi"
import { message } from "@/components/bs-ui/toast/use-toast"
import {
    getServiceAccountApi,
    issueServiceAccountKeyApi,
    updateServiceAccountKeyApi,
} from "@/controllers/API/serviceAccount"
import { captureAndAlertRequestErrorHoc } from "@/controllers/request"
import {
    AgentRef,
    AssignableAgent,
    OpenApiScope,
    ServiceAccountKey,
    ServiceAccountKeyIssue,
    ServiceAccountKeyIssued,
    ServiceAccountKeyUpdate,
} from "@/types/api/serviceAccount"
import { useEffect, useMemo, useState } from "react"
import { useTranslation } from "react-i18next"

/**
 * Locale key for one scope code. The codes contain a colon, which i18next
 * reads as its namespace separator (`ns:key`), so the locale files use an
 * underscore instead: `workflow:invoke` -> `scope.workflow_invoke`.
 */
export function scopeLocaleKey(code: string): string {
    return `scope.${code.replace(/:/g, "_")}`
}

/** The picker encodes an agent as `${type}:${id}` to keep one flat multi-select. */
function agentOptionValue(ref: AgentRef): string {
    return `${ref.type}:${ref.id}`
}

function parseAgentOption(value: string): AgentRef | null {
    const separator = value.indexOf(":")
    if (separator <= 0) return null
    const type = value.slice(0, separator)
    const id = value.slice(separator + 1)
    if (!id || (type !== "assistant" && type !== "workflow" && type !== "managed_agent")) return null
    return { type, id }
}

function sameAgentSet(left: AgentRef[], right: AgentRef[]): boolean {
    if (left.length !== right.length) return false
    const keys = new Set(left.map(agentOptionValue))
    return right.every((ref) => keys.has(agentOptionValue(ref)))
}

/** Expiry presets offered on top of "never" and a custom datetime. */
const PRESET_DAYS: Record<string, number> = { d90: 90, d180: 180, d365: 365 }

/** Scope each agent type needs for the allowlist entry to be callable at all. */
const AGENT_INVOKE_SCOPE: Record<AgentRef["type"], string> = {
    assistant: "assistant:invoke",
    workflow: "workflow:invoke",
    managed_agent: "managed_agent:invoke",
}

/**
 * Naive local ISO string. The backend stores and compares naive local time
 * (its own serialiser emits no timezone), so `toISOString()` would shift the
 * instant by the UTC offset.
 */
function toNaiveIso(date: Date): string {
    const pad = (value: number) => String(value).padStart(2, "0")
    const ymd = `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`
    return `${ymd}T${pad(date.getHours())}:${pad(date.getMinutes())}:${pad(date.getSeconds())}`
}

/** Second granularity: the backend serialises microseconds, which would look
 *  like a change on every save and silently truncate them. */
function sameExpiry(left: string | null, right: string | null): boolean {
    if (left === null || right === null) return left === right
    return Math.floor(new Date(left).getTime() / 1000) === Math.floor(new Date(right).getTime() / 1000)
}

function sameScopes(left: string[], right: string[]): boolean {
    return left.length === right.length && left.every((code) => right.includes(code))
}

interface KeyIssueDialogProps {
    open: boolean
    accountId: number
    /** Catalog from the backend; a key may only carry registered scopes. */
    scopes: OpenApiScope[]
    /** Agents the account may be restricted to (backend `assignable-agents`). */
    assignableAgents: AssignableAgent[]
    /** Set = edit mode. PATCH only carries the fields that actually changed. */
    editing?: ServiceAccountKey | null
    onClose: (open: boolean) => void
    /** Switches to the grants tab; closing this dialog first is the caller's job. */
    onGoToGrants?: () => void
    /** Creating only: the response carries the plaintext for the issue dialog. */
    onIssued: (issued: ServiceAccountKeyIssued, name: string) => void
    onUpdated: () => void
}

export function KeyIssueDialog({
    open,
    accountId,
    scopes,
    assignableAgents,
    editing = null,
    onClose,
    onGoToGrants,
    onIssued,
    onUpdated,
}: KeyIssueDialogProps) {
    const { t } = useTranslation("serviceAccount")
    const [name, setName] = useState("")
    const [selected, setSelected] = useState<string[]>([])
    // New keys start restricted (least privilege); edit mode mirrors the saved
    // null-vs-list state so an unrestricted legacy key stays unrestricted.
    const [restrictAgents, setRestrictAgents] = useState(true)
    const [selectedAgents, setSelectedAgents] = useState<string[]>([])
    const [expiryMode, setExpiryMode] = useState("never")
    const [customExpiry, setCustomExpiry] = useState<Date | null>(null)
    const [submitting, setSubmitting] = useState(false)

    // Re-seed on every open so a cancelled edit never leaks into the next one.
    useEffect(() => {
        if (!open) return
        setName(editing?.name ?? "")
        setSelected(editing ? [...editing.scopes] : [])
        if (editing) {
            setRestrictAgents(editing.allowed_apps !== null)
            setSelectedAgents((editing.allowed_apps ?? []).map(agentOptionValue))
        } else {
            setRestrictAgents(true)
            setSelectedAgents([])
        }
        setExpiryMode(editing?.expires_at ? "custom" : "never")
        setCustomExpiry(editing?.expires_at ? new Date(editing.expires_at) : null)
    }, [open, editing])

    /**
     * Picker options = currently granted agents + any saved entry that is no
     * longer assignable (grant revoked). The latter must stay visible so a save
     * never silently drops a restriction the admin cannot see.
     */
    const agentOptions = useMemo(() => {
        const options = assignableAgents.map((agent) => ({
            value: agentOptionValue(agent),
            label: `${t(`keys.agents.type.${agent.type}`)} · ${agent.name}${
                agent.invocable ? "" : ` (${t("keys.agents.readOnly")})`
            }`,
        }))
        if (!editing?.allowed_apps) return options
        const known = new Set(options.map((option) => option.value))
        for (const ref of editing.allowed_apps) {
            const value = agentOptionValue(ref)
            if (known.has(value)) continue
            options.push({
                value,
                label: `${t(`keys.agents.type.${ref.type}`)} · ${ref.id} (${t("keys.agents.stale")})`,
            })
        }
        return options
    }, [assignableAgents, editing, t])

    /** Scopes implied by the selected agents but absent from the key. Advisory
     *  only: a knowledge-only key is legitimate, so this never blocks submit. */
    const missingAgentScopes = useMemo(() => {
        if (!restrictAgents || selectedAgents.length === 0) return []
        const needed = new Set<string>()
        for (const value of selectedAgents) {
            const ref = parseAgentOption(value)
            if (ref) needed.add(AGENT_INVOKE_SCOPE[ref.type])
        }
        return [...needed].filter((code) => !selected.includes(code))
    }, [restrictAgents, selectedAgents, selected])

    /**
     * True when saving would persist `allowed_apps: []` -- an allowlist that
     * matches no agent, so the key can call nothing. Creating one is always a
     * mistake; editing a legacy empty allowlist stays allowed so the key can
     * still be renamed.
     */
    const wouldWriteEmptyAllowlist = () =>
        restrictAgents && selectedAgents.length === 0 && (!editing || (editing.allowed_apps?.length ?? 0) > 0)

    /** null = unrestricted; [] = no agent; otherwise the selected refs. */
    const buildAllowedApps = (): AgentRef[] | null => {
        if (!restrictAgents) return null
        return selectedAgents.map(parseAgentOption).filter((ref): ref is AgentRef => ref !== null)
    }

    /**
     * Resolve the expiry to send. `baseline` is the key's current expiry when
     * editing (null when creating). The "must be in the future" rule only binds a
     * NEW value -- otherwise an already expired key could not even be renamed
     * without silently re-activating it.
     */
    const resolveExpiry = (baseline: string | null): { value: string | null; error?: string } => {
        let value: string | null
        if (expiryMode === "never") {
            value = null
        } else if (expiryMode === "custom") {
            if (!customExpiry) return { value: null, error: t("keys.form.expiryRequired") }
            value = toNaiveIso(customExpiry)
        } else {
            const next = new Date()
            next.setDate(next.getDate() + PRESET_DAYS[expiryMode])
            value = toNaiveIso(next)
        }
        // The backend accepts a past datetime and hands back a key that is dead on
        // arrival, so the guard has to live here.
        if (value !== null && !sameExpiry(value, baseline) && new Date(value).getTime() <= Date.now()) {
            return { value: null, error: t("keys.form.expiryPast") }
        }
        return { value }
    }

    const handleConfirm = () => {
        const trimmed = name.trim()
        if (!trimmed) {
            message({ title: t("prompt"), description: t("keys.form.nameRequired"), variant: "warning" })
            return
        }
        if (trimmed.length > 128) {
            message({ title: t("prompt"), description: t("keys.form.nameTooLong"), variant: "warning" })
            return
        }
        const expiry = resolveExpiry(editing?.expires_at ?? null)
        if (expiry.error) {
            message({ title: t("prompt"), description: expiry.error, variant: "warning" })
            return
        }
        // Restricted keys must name at least one agent; an empty allowlist used
        // to be written without any guard (the old code only warned when an
        // invoke scope happened to be selected).
        if (wouldWriteEmptyAllowlist()) {
            message({
                title: t("prompt"),
                description:
                    assignableAgents.length === 0
                        ? t("keys.form.agentsNeedGrant")
                        : t("keys.form.agentsEmptyWarning"),
                variant: "warning",
            })
            return
        }

        setSubmitting(true)
        if (editing) {
            const payload: ServiceAccountKeyUpdate = {}
            if (trimmed !== (editing.name ?? "")) payload.name = trimmed
            // Never send null: `scopes` maps to a NOT NULL column the endpoint
            // would write through unchecked.
            if (!sameScopes(selected, editing.scopes)) payload.scopes = selected
            const allowedApps = buildAllowedApps()
            const unchangedAgents =
                editing.allowed_apps === null
                    ? allowedApps === null
                    : allowedApps !== null && sameAgentSet(allowedApps, editing.allowed_apps)
            if (!unchangedAgents) payload.allowed_apps = allowedApps
            if (!sameExpiry(expiry.value, editing.expires_at)) payload.expires_at = expiry.value
            if (!Object.keys(payload).length) {
                setSubmitting(false)
                onClose(false)
                return
            }
            captureAndAlertRequestErrorHoc(updateServiceAccountKeyApi(accountId, editing.id, payload))
                .then((res) => {
                    if (!res) return
                    message({ title: t("prompt"), description: t("keys.updateSuccess"), variant: "success" })
                    onClose(false)
                    onUpdated()
                })
                .finally(() => setSubmitting(false))
            return
        }

        // A disabled account answers the issue call with 11805, which travels as a
        // real HTTP 401 -- and the request wrapper treats any 401 as an expired
        // session (wipes the token, redirects to the login page). The account state
        // on screen can be stale (disabled from another tab), so re-read it here
        // instead of letting the backend kick the admin out of the console.
        void (async () => {
            const fresh = await captureAndAlertRequestErrorHoc(getServiceAccountApi(accountId))
            if (!fresh) {
                setSubmitting(false)
                return
            }
            if (!fresh.enabled) {
                message({ title: t("prompt"), description: t("keys.accountDisabledHint"), variant: "warning" })
                setSubmitting(false)
                onClose(false)
                onUpdated()
                return
            }
            const payload: ServiceAccountKeyIssue = {
                name: trimmed,
                scopes: selected,
                allowed_apps: buildAllowedApps(),
                expires_at: expiry.value,
            }
            captureAndAlertRequestErrorHoc(issueServiceAccountKeyApi(accountId, payload))
                .then((res) => {
                    if (!res) return
                    // Close first, then hand the plaintext to the reveal dialog: it
                    // is never held anywhere else.
                    onClose(false)
                    onIssued(res, trimmed)
                })
                .finally(() => setSubmitting(false))
        })()
    }

    return (
        <Dialog
            open={open}
            onOpenChange={(next) => {
                if (next) return
                // The POST is already on the wire: letting the form vanish now would
                // drop the one-time plaintext while the key itself still gets created.
                if (submitting) return
                onClose(false)
            }}
        >
            <DialogContent className="sm:max-w-[560px]" close={!submitting}>
                <DialogHeader>
                    <DialogTitle>{editing ? t("keys.editTitle") : t("keys.issueTitle")}</DialogTitle>
                </DialogHeader>
                <div className="flex flex-col gap-6">
                    <div>
                        <Label className="bisheng-label">{t("keys.form.name")}</Label>
                        <Input
                            value={name}
                            maxLength={128}
                            placeholder={t("keys.form.namePlaceholder")}
                            onChange={(e) => setName(e.target.value)}
                        />
                    </div>
                    <div>
                        <Label className="bisheng-label">{t("keys.form.scopes")}</Label>
                        <MultiSelect
                            multiple
                            className="mt-1"
                            options={scopes.map((scope) => ({
                                label: `${t(scopeLocaleKey(scope.code), { defaultValue: scope.code })} (${scope.code})`,
                                value: scope.code,
                            }))}
                            value={selected}
                            onChange={(values) => setSelected(values as string[])}
                            placeholder={t("keys.form.scopesPlaceholder")}
                            searchPlaceholder={t("keys.form.scopesSearch")}
                        />
                    </div>
                    <div>
                        <label className="flex items-center gap-2 text-sm">
                            <Checkbox
                                checked={restrictAgents}
                                onCheckedChange={(value) => setRestrictAgents(!!value)}
                            />
                            {t("keys.form.restrictAgents")}
                        </label>
                        <p className="mt-1 text-xs text-muted-foreground">
                            {restrictAgents ? t("keys.form.restrictAgentsHint") : t("keys.form.agentsUnrestrictedHint")}
                        </p>
                        {restrictAgents && (
                            <div className="mt-2">
                                <MultiSelect
                                    multiple
                                    options={agentOptions}
                                    value={selectedAgents}
                                    onChange={(values) => setSelectedAgents(values as string[])}
                                    placeholder={t("keys.form.agentsPlaceholder")}
                                    searchPlaceholder={t("keys.form.agentsSearch")}
                                />
                                {selectedAgents.length === 0 && (
                                    <p className="mt-1 text-xs text-amber-600">
                                        {assignableAgents.length === 0
                                            ? t("keys.form.agentsNeedGrant")
                                            : t("keys.form.agentsEmptyWarning")}
                                    </p>
                                )}
                                {assignableAgents.length === 0 && onGoToGrants && (
                                    <Button
                                        variant="link"
                                        size="sm"
                                        className="mt-1 h-auto p-0"
                                        onClick={() => {
                                            onClose(false)
                                            onGoToGrants()
                                        }}
                                    >
                                        {t("keys.form.goToGrants")}
                                    </Button>
                                )}
                                {selectedAgents.length > 0 && missingAgentScopes.length > 0 && (
                                    <p className="mt-1 text-xs text-amber-600">
                                        {t("keys.form.agentsMissingScopes", {
                                            scopes: missingAgentScopes
                                                .map((code) => t(scopeLocaleKey(code), { defaultValue: code }))
                                                .join(", "),
                                        })}
                                    </p>
                                )}
                            </div>
                        )}
                    </div>
                    <div>
                        <Label className="bisheng-label">{t("keys.form.expires")}</Label>
                        <Select value={expiryMode} onValueChange={setExpiryMode}>
                            <SelectTrigger className="mt-1">
                                <SelectValue />
                            </SelectTrigger>
                            <SelectContent>
                                <SelectItem value="never">{t("keys.form.never")}</SelectItem>
                                <SelectItem value="d90">{t("keys.form.days90")}</SelectItem>
                                <SelectItem value="d180">{t("keys.form.days180")}</SelectItem>
                                <SelectItem value="d365">{t("keys.form.days365")}</SelectItem>
                                <SelectItem value="custom">{t("keys.form.custom")}</SelectItem>
                            </SelectContent>
                        </Select>
                        {expiryMode === "custom" && (
                            <div className="mt-2">
                                <DatePicker
                                    showTime
                                    value={customExpiry ?? undefined}
                                    placeholder={t("keys.form.customPlaceholder")}
                                    onChange={(date) => setCustomExpiry(date)}
                                />
                            </div>
                        )}
                        <p className="mt-2 text-xs text-muted-foreground">{t("keys.form.expiryHint")}</p>
                    </div>
                </div>
                <DialogFooter className="mt-6">
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
