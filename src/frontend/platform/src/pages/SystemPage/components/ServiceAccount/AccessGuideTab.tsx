// F076: the "接入指南" sub-tab puts the API-access handover material inside the
// product, so an admin no longer has to dictate interface addresses and calling
// conventions offline. The body ships as a bundled Markdown asset (imported
// with ?raw): no network hop, no failure state — and because the on-screen
// render and the copy/export text both go through buildGuideText, what the
// admin reads is exactly what the integrator receives.
import { MarkdownDoc } from "@/components/bs-comp/markdown/MarkdownDoc"
import { Button } from "@/components/bs-ui/button"
import { Input } from "@/components/bs-ui/input"
import { toast } from "@/components/bs-ui/toast/use-toast"
import { getOpenApiBaseUrlApi } from "@/controllers/API/serviceAccount"
import { copyText } from "@/utils"
import { formatDate, formatIsoDateTime } from "@/util/utils"
import { Check, Download } from "lucide-react"
import { useEffect, useMemo, useRef, useState } from "react"
import { useTranslation } from "react-i18next"
import GUIDE_MD from "./accessGuide.md?raw"
import {
    applyGuidePlaceholders,
    buildGuideText,
    clearStoredGuideBaseUrl,
    GuideBaseUrlSource,
    isDevServer,
    normalizeAccessBaseUrl,
    readStoredGuideBaseUrl,
    resolveGuideBaseUrl,
    storeGuideBaseUrl,
} from "./accessGuide"

const COPIED_RESET_MS = 2000

/** i18n key for each provenance label shown next to the address input. */
const SOURCE_LABEL_KEYS: Record<GuideBaseUrlSource, string> = {
    configured: "guide.baseUrlSourceConfigured",
    stored: "guide.baseUrlSourceStored",
    detected: "guide.baseUrlSourceDetected",
    console: "guide.baseUrlSourceConsole",
}

export function AccessGuideTab() {
    const { t } = useTranslation("serviceAccount")
    // Resolve once at mount: the backend lookup below refines this default; it
    // must not re-read localStorage, where an address saved after mount (by an
    // edit) would masquerade as a pre-existing override.
    const [initial] = useState(() =>
        resolveGuideBaseUrl({ stored: readStoredGuideBaseUrl(), isDev: isDevServer() }),
    )
    const storedAtMount = initial.source === "stored" ? initial.url : null
    // Committed address (drives both the rendered body and the export) vs. the
    // draft the admin is typing: an invalid draft only shows the inline error
    // and never replaces the committed value, so the document can't go bad
    // halfway through an edit.
    const [baseUrl, setBaseUrl] = useState(initial.url)
    const [draft, setDraft] = useState(initial.url)
    const [source, setSource] = useState<GuideBaseUrlSource>(initial.source)
    const [invalid, setInvalid] = useState(false)
    const [copied, setCopied] = useState(false)
    const copiedTimer = useRef<number | undefined>(undefined)
    // Once the admin edits or resets, the late-arriving backend answer must not
    // overwrite their choice — it only fills in a default nobody has touched.
    const edited = useRef(false)
    const candidates = useRef<{ configured?: string | null; detected?: string | null }>({})
    // Mirror of the committed pair; lets the async effect skip re-rendering when
    // the backend confirms the default already on screen.
    const committed = useRef({ url: initial.url, source: initial.source })

    useEffect(() => {
        let cancelled = false
        // Best effort: an older backend (404) or a transient error must leave the
        // synchronous console-origin default in place rather than break the tab.
        getOpenApiBaseUrlApi()
            .then((sources) => {
                if (cancelled) return
                candidates.current = sources
                if (edited.current) return
                const next = resolveGuideBaseUrl({
                    ...sources,
                    stored: storedAtMount,
                    isDev: isDevServer(),
                })
                if (committed.current.url === next.url && committed.current.source === next.source) return
                committed.current = { url: next.url, source: next.source }
                setBaseUrl(next.url)
                setDraft(next.url)
                setSource(next.source)
            })
            .catch(() => {
                // No address sources available — the console-origin default stands.
            })
        return () => {
            cancelled = true
        }
        // `storedAtMount` is derived once from mount state, so this stays a
        // single lookup per mount.
    }, [storedAtMount])

    const rendered = useMemo(() => applyGuidePlaceholders(GUIDE_MD, baseUrl), [baseUrl])

    const codeCopy = useMemo(
        () => ({ copy: t("guide.codeCopy"), copied: t("guide.copied"), failed: t("guide.copyFailed") }),
        [t],
    )

    const exportText = useMemo(
        () =>
            buildGuideText(GUIDE_MD, baseUrl, [
                t("guide.exportHeaderSource"),
                t("guide.exportHeaderTime", { time: formatIsoDateTime(new Date().toISOString()) }),
                t("guide.exportHeaderAddress", { url: baseUrl }),
            ]),
        [t, baseUrl],
    )

    const commitDraft = () => {
        const next = normalizeAccessBaseUrl(draft)
        if (!next) {
            setInvalid(true)
            return
        }
        edited.current = true
        setInvalid(false)
        setDraft(next)
        setBaseUrl(next)
        setSource("stored")
        committed.current = { url: next, source: "stored" }
        storeGuideBaseUrl(next)
    }

    const handleReset = () => {
        // Back to the deployment default, which may be the ops config or the
        // backend-detected address rather than this console's origin.
        const next = resolveGuideBaseUrl({ ...candidates.current, isDev: isDevServer() })
        // If the source lookup has not returned yet, let it land afterwards — it
        // resolves to the same default the admin just asked for.
        edited.current = candidates.current.configured !== undefined
        setInvalid(false)
        setDraft(next.url)
        setBaseUrl(next.url)
        setSource(next.source)
        committed.current = { url: next.url, source: next.source }
        clearStoredGuideBaseUrl()
    }

    const handleCopy = () => {
        // The execCommand fallback in copyText resolves regardless of outcome,
        // so without a clipboard API we must not claim "copied".
        const canVerify = typeof navigator !== "undefined" && !!navigator.clipboard?.writeText
        copyText(exportText)
            .then(() => {
                if (!canVerify) {
                    toast({ variant: "warning", description: t("guide.copyManualHint") })
                    return
                }
                setCopied(true)
                window.clearTimeout(copiedTimer.current)
                copiedTimer.current = window.setTimeout(() => setCopied(false), COPIED_RESET_MS)
            })
            // copyText lets a denied clipboard rejection through unguarded.
            .catch(() => toast({ variant: "warning", description: t("guide.copyFailed") }))
    }

    const handleExport = () => {
        try {
            const blob = new Blob([exportText], { type: "text/markdown;charset=utf-8" })
            const url = URL.createObjectURL(blob)
            const link = document.createElement("a")
            link.href = url
            link.download = t("guide.exportFileName", { date: formatDate(new Date(), "yyyyMMdd") })
            link.click()
            // Revoke on the next tick: same-tick revocation can abort the download.
            window.setTimeout(() => URL.revokeObjectURL(url), 0)
        } catch {
            toast({ variant: "warning", description: t("guide.exportFailed") })
        }
    }

    return (
        <div className="flex h-full min-h-0 flex-col gap-3">
            <p className="shrink-0 text-sm text-muted-foreground">{t("guide.intro")}</p>

            <div className="flex shrink-0 flex-wrap items-end gap-3">
                <div className="w-[420px] max-w-full">
                    <div className="mb-2 flex items-center gap-2">
                        <label className="bisheng-label" htmlFor="open-api-guide-base-url">
                            {t("guide.baseUrlLabel")}
                        </label>
                        <span
                            data-source={source}
                            className="rounded bg-muted px-1.5 py-0.5 text-[11px] text-muted-foreground"
                        >
                            {t("guide.baseUrlSourceLabel", { source: t(SOURCE_LABEL_KEYS[source]) })}
                        </span>
                    </div>
                    <Input
                        id="open-api-guide-base-url"
                        value={draft}
                        aria-invalid={invalid}
                        placeholder={t("guide.baseUrlPlaceholder")}
                        onChange={(e) => setDraft(e.target.value)}
                        onBlur={commitDraft}
                        onKeyDown={(e) => {
                            if (e.key === "Enter") commitDraft()
                        }}
                    />
                </div>
                <Button variant="link" onClick={handleReset}>
                    {t("guide.baseUrlReset")}
                </Button>
                <div className="flex-1" />
                <Button variant="outline" onClick={handleCopy}>
                    {copied && <Check className="mr-1 h-4 w-4" />}
                    {copied ? t("guide.copied") : t("guide.copy")}
                </Button>
                <Button onClick={handleExport}>
                    <Download className="mr-1 h-4 w-4" />
                    {t("guide.export")}
                </Button>
            </div>

            <p className={`shrink-0 text-xs ${invalid ? "text-red-500" : "text-muted-foreground"}`}>
                {invalid ? t("guide.baseUrlInvalid") : t("guide.baseUrlHint")}
            </p>

            <div className="min-h-0 flex-1 overflow-y-auto rounded-lg border bg-background px-6 py-5">
                <div className="mx-auto max-w-[860px]">
                    <MarkdownDoc markdown={rendered} codeCopy={codeCopy} />
                </div>
            </div>
        </div>
    )
}
