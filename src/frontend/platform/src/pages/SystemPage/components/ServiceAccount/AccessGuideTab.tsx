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
import { copyText } from "@/utils"
import { formatDate, formatIsoDateTime } from "@/util/utils"
import { Check, Download } from "lucide-react"
import { useMemo, useRef, useState } from "react"
import { useTranslation } from "react-i18next"
import GUIDE_MD from "./accessGuide.md?raw"
import {
    applyGuidePlaceholders,
    buildGuideText,
    defaultGuideBaseUrl,
    normalizeAccessBaseUrl,
    readStoredGuideBaseUrl,
    storeGuideBaseUrl,
} from "./accessGuide"

const COPIED_RESET_MS = 2000

export function AccessGuideTab() {
    const { t } = useTranslation("serviceAccount")
    // Committed address (drives both the rendered body and the export) vs. the
    // draft the admin is typing: an invalid draft only shows the inline error
    // and never replaces the committed value, so the document can't go bad
    // halfway through an edit.
    const [baseUrl, setBaseUrl] = useState(() => readStoredGuideBaseUrl() ?? defaultGuideBaseUrl())
    const [draft, setDraft] = useState(baseUrl)
    const [invalid, setInvalid] = useState(false)
    const [copied, setCopied] = useState(false)
    const copiedTimer = useRef<number | undefined>(undefined)

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
        setInvalid(false)
        setDraft(next)
        setBaseUrl(next)
        storeGuideBaseUrl(next)
    }

    const handleReset = () => {
        const next = defaultGuideBaseUrl()
        setInvalid(false)
        setDraft(next)
        setBaseUrl(next)
        storeGuideBaseUrl(next)
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
                    <label className="bisheng-label mb-2 block" htmlFor="open-api-guide-base-url">
                        {t("guide.baseUrlLabel")}
                    </label>
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
