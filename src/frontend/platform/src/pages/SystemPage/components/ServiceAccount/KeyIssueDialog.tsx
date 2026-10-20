import { Button } from "@/components/bs-ui/button"
import { DatePicker } from "@/components/bs-ui/calendar/datePicker"
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
    OpenApiScope,
    ServiceAccountKey,
    ServiceAccountKeyIssue,
    ServiceAccountKeyIssued,
    ServiceAccountKeyUpdate,
} from "@/types/api/serviceAccount"
import { useEffect, useState } from "react"
import { useTranslation } from "react-i18next"

/**
 * Locale key for one scope code. The codes contain a colon, which i18next
 * reads as its namespace separator (`ns:key`), so the locale files use an
 * underscore instead: `workflow:invoke` -> `scope.workflow_invoke`.
 */
export function scopeLocaleKey(code: string): string {
    return `scope.${code.replace(/:/g, "_")}`
}

/** Expiry presets offered on top of "never" and a custom datetime. */
const PRESET_DAYS: Record<string, number> = { d90: 90, d180: 180, d365: 365 }

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
    /** Set = edit mode. PATCH only carries the fields that actually changed. */
    editing?: ServiceAccountKey | null
    onClose: (open: boolean) => void
    /** Creating only: the response carries the one-time plaintext. */
    onIssued: (issued: ServiceAccountKeyIssued, name: string) => void
    onUpdated: () => void
}

export function KeyIssueDialog({
    open,
    accountId,
    scopes,
    editing = null,
    onClose,
    onIssued,
    onUpdated,
}: KeyIssueDialogProps) {
    const { t } = useTranslation("serviceAccount")
    const [name, setName] = useState("")
    const [selected, setSelected] = useState<string[]>([])
    const [expiryMode, setExpiryMode] = useState("never")
    const [customExpiry, setCustomExpiry] = useState<Date | null>(null)
    const [submitting, setSubmitting] = useState(false)

    // Re-seed on every open so a cancelled edit never leaks into the next one.
    useEffect(() => {
        if (!open) return
        setName(editing?.name ?? "")
        setSelected(editing ? [...editing.scopes] : [])
        setExpiryMode(editing?.expires_at ? "custom" : "never")
        setCustomExpiry(editing?.expires_at ? new Date(editing.expires_at) : null)
    }, [open, editing])

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

        setSubmitting(true)
        if (editing) {
            const payload: ServiceAccountKeyUpdate = {}
            if (trimmed !== (editing.name ?? "")) payload.name = trimmed
            // Never send null: `scopes` maps to a NOT NULL column the endpoint
            // would write through unchecked.
            if (!sameScopes(selected, editing.scopes)) payload.scopes = selected
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
            const payload: ServiceAccountKeyIssue = { name: trimmed, scopes: selected, expires_at: expiry.value }
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
