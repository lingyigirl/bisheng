import { Alert, AlertDescription, AlertTitle } from "@/components/bs-ui/alert"
import { Button } from "@/components/bs-ui/button"
import { Checkbox } from "@/components/bs-ui/checkBox"
import { Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle } from "@/components/bs-ui/dialog"
import { message } from "@/components/bs-ui/toast/use-toast"
import { copyText } from "@/utils"
import { Check, Clipboard } from "lucide-react"
import { useEffect, useState } from "react"
import { useTranslation } from "react-i18next"

type KeyRevealMode = "issued" | "viewed"

interface KeyRevealDialogProps {
    open: boolean
    keyName: string | null
    /** Plaintext fetched for this dialog; the caller drops it on close. */
    plaintext: string
    /**
     * `issued` = right after creation (must confirm it was saved, AC-39).
     * `viewed` = an explicit admin reveal (audited, can be closed freely).
     */
    mode?: KeyRevealMode
    onClose: () => void
}

/**
 * Plaintext reveal (F076 AC-39 / AC-44).
 *
 * In `issued` mode the dialog is deliberately not dismissible by the X button,
 * the overlay or Esc: closing must go through the "I saved it" confirmation.
 * In `viewed` mode the admin explicitly requested the value and the backend has
 * already written an audit row, so normal dismissal is allowed.
 */
export function KeyRevealDialog({ open, keyName, plaintext, mode = "issued", onClose }: KeyRevealDialogProps) {
    const { t } = useTranslation("serviceAccount")
    const [confirmed, setConfirmed] = useState(false)
    const [copied, setCopied] = useState(false)
    const issued = mode === "issued"

    useEffect(() => {
        if (open) {
            setConfirmed(false)
            setCopied(false)
        }
    }, [open])

    const handleCopy = () => {
        copyText(plaintext)
            .then(() => {
                setCopied(true)
                setTimeout(() => setCopied(false), 2000)
            })
            .catch(() => {
                // Clipboard writes reject on insecure origins and on unfocused
                // documents: tell the operator to select the text by hand rather
                // than let a green checkmark imply the key was saved.
                message({ title: t("prompt"), description: t("keys.reveal.copyFailed"), variant: "warning" })
            })
    }

    return (
        <Dialog
            open={open}
            onOpenChange={(next) => {
                if (next) return
                // Issued mode: refuse overlay/Esc until confirmed. Viewed mode:
                // the value was explicitly requested, so normal dismissal is fine.
                if (issued && !confirmed) return
                onClose()
            }}
        >
            <DialogContent className="sm:max-w-[560px]" close={!issued}>
                <DialogHeader>
                    <DialogTitle>{issued ? t("keys.reveal.title") : t("keys.reveal.viewTitle")}</DialogTitle>
                </DialogHeader>

                <Alert variant={issued ? "destructive" : "default"}>
                    <AlertTitle>
                        {issued ? t("keys.reveal.warningTitle") : t("keys.reveal.viewWarningTitle")}
                    </AlertTitle>
                    <AlertDescription>
                        {issued ? t("keys.reveal.warningDesc") : t("keys.reveal.viewWarningDesc")}
                    </AlertDescription>
                </Alert>

                {keyName && <p className="text-sm text-muted-foreground">{t("keys.reveal.keyName", { name: keyName })}</p>}

                <div className="flex items-center gap-2">
                    <code className="min-w-0 flex-1 select-all break-all rounded bg-muted px-3 py-2 font-mono text-sm">
                        {plaintext}
                    </code>
                    <Button variant="outline" size="sm" className="shrink-0" disabled={!plaintext} onClick={handleCopy}>
                        {copied ? <Check className="h-4 w-4" /> : <Clipboard className="h-4 w-4" />}
                        <span className="ml-1">{t("keys.reveal.copy")}</span>
                    </Button>
                </div>

                {issued && (
                    <label className="flex items-center gap-2 text-sm">
                        <Checkbox checked={confirmed} onCheckedChange={(value) => setConfirmed(!!value)} />
                        {t("keys.reveal.confirmSaved")}
                    </label>
                )}

                <DialogFooter className="mt-2">
                    <Button className="h-10 w-[120px] px-16" disabled={issued && !confirmed} onClick={onClose}>
                        {issued ? t("keys.reveal.done") : t("keys.reveal.close")}
                    </Button>
                </DialogFooter>
            </DialogContent>
        </Dialog>
    )
}
