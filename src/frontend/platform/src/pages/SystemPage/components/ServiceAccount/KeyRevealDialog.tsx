import { Alert, AlertDescription, AlertTitle } from "@/components/bs-ui/alert"
import { Button } from "@/components/bs-ui/button"
import { Checkbox } from "@/components/bs-ui/checkBox"
import { Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle } from "@/components/bs-ui/dialog"
import { message } from "@/components/bs-ui/toast/use-toast"
import { copyText } from "@/utils"
import { Check, Clipboard } from "lucide-react"
import { useEffect, useState } from "react"
import { useTranslation } from "react-i18next"

interface KeyRevealDialogProps {
    open: boolean
    keyName: string | null
    /** The one-time plaintext. Held by the caller only while this dialog is open. */
    plaintext: string
    /** Only ever called once the operator confirmed the key was saved (AC-39). */
    onClose: () => void
}

/**
 * One-time plaintext reveal (F076 AC-39).
 *
 * The plaintext exists in exactly one API response and is never retrievable
 * again, so this dialog is deliberately not dismissible by the X button, the
 * overlay or Esc: closing must go through the "I saved it" confirmation.
 */
export function KeyRevealDialog({ open, keyName, plaintext, onClose }: KeyRevealDialogProps) {
    const { t } = useTranslation("serviceAccount")
    const [confirmed, setConfirmed] = useState(false)
    const [copied, setCopied] = useState(false)

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
                // Radix reports overlay clicks / Esc here; refuse until confirmed.
                if (next || !confirmed) return
                onClose()
            }}
        >
            <DialogContent className="sm:max-w-[560px]" close={false}>
                <DialogHeader>
                    <DialogTitle>{t("keys.reveal.title")}</DialogTitle>
                </DialogHeader>

                <Alert variant="destructive">
                    <AlertTitle>{t("keys.reveal.warningTitle")}</AlertTitle>
                    <AlertDescription>{t("keys.reveal.warningDesc")}</AlertDescription>
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

                <label className="flex items-center gap-2 text-sm">
                    <Checkbox checked={confirmed} onCheckedChange={(value) => setConfirmed(!!value)} />
                    {t("keys.reveal.confirmSaved")}
                </label>

                <DialogFooter className="mt-2">
                    <Button className="h-10 w-[120px] px-16" disabled={!confirmed} onClick={onClose}>
                        {t("keys.reveal.done")}
                    </Button>
                </DialogFooter>
            </DialogContent>
        </Dialog>
    )
}
