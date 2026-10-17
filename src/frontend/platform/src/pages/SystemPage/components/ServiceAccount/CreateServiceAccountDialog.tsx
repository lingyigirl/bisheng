import { Button } from "@/components/bs-ui/button"
import { Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle } from "@/components/bs-ui/dialog"
import { Input, Textarea } from "@/components/bs-ui/input"
import { Label } from "@/components/bs-ui/label"
import { useToast } from "@/components/bs-ui/toast/use-toast"
import { createServiceAccountApi } from "@/controllers/API/serviceAccount"
import { captureAndAlertRequestErrorHoc } from "@/controllers/request"
import { ServiceAccount } from "@/types/api/serviceAccount"
import { useEffect, useState } from "react"
import { useTranslation } from "react-i18next"

interface CreateServiceAccountDialogProps {
    open: boolean
    onClose: (open: boolean) => void
    /** Called after a successful create: the list refreshes and the caller can
     *  offer the "issue the first key" next step with the returned account. */
    onCreated: (account: ServiceAccount) => void
}

/**
 * Create one service account. The backend creates the dedicated resource owner
 * (svc-*) in the same transaction, so there is nothing else to configure here.
 */
export function CreateServiceAccountDialog({ open, onClose, onCreated }: CreateServiceAccountDialogProps) {
    const { t } = useTranslation("serviceAccount")
    const { message } = useToast()
    const [name, setName] = useState("")
    const [description, setDescription] = useState("")
    const [submitting, setSubmitting] = useState(false)

    const reset = () => {
        setName("")
        setDescription("")
    }

    // Cancel closes the dialog through the parent (setOpen(false)), and a parent
    // flip never fires Radix's onOpenChange -- so clearing on open is what keeps a
    // discarded draft from reappearing next time.
    useEffect(() => {
        if (!open) return
        setName("")
        setDescription("")
    }, [open])

    const handleConfirm = () => {
        const errors: string[] = []
        if (!name.trim()) errors.push(t("form.nameRequired"))
        if (name.trim().length > 128) errors.push(t("form.nameTooLong"))
        if (errors.length) {
            message({ title: t("prompt"), description: errors, variant: "warning" })
            return
        }

        setSubmitting(true)
        captureAndAlertRequestErrorHoc(
            createServiceAccountApi({ name: name.trim(), description: description.trim() || null }).then((account) => {
                if (!account) return
                message({ title: t("prompt"), description: t("createSuccess"), variant: "success" })
                reset()
                onClose(false)
                onCreated(account)
            }),
        ).finally(() => setSubmitting(false))
    }

    return (
        <Dialog
            open={open}
            onOpenChange={(next) => {
                if (!next) reset()
                onClose(next)
            }}
        >
            <DialogContent className="sm:max-w-[560px]">
                <DialogHeader>
                    <DialogTitle>{t("dialog.createTitle")}</DialogTitle>
                </DialogHeader>
                <div className="flex flex-col gap-6">
                    <div className="">
                        <Label className="bisheng-label">{t("form.name")}</Label>
                        <Input
                            value={name}
                            maxLength={128}
                            placeholder={t("form.namePlaceholder")}
                            onChange={(e) => setName(e.target.value)}
                        />
                    </div>
                    <div className="">
                        <Label className="bisheng-label">{t("form.description")}</Label>
                        <Textarea
                            value={description}
                            maxLength={512}
                            placeholder={t("form.descriptionPlaceholder")}
                            onChange={(e) => setDescription(e.target.value)}
                        />
                    </div>
                    {/* The owner identity is platform-managed; explaining it here
                        avoids the "which user should I pick?" question entirely. */}
                    <p className="text-sm text-muted-foreground">{t("form.ownerNotice")}</p>
                </div>
                <DialogFooter className="mt-6">
                    <Button variant="outline" className="h-10 w-[120px] px-16" onClick={() => onClose(false)}>
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
