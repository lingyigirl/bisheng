import { Button } from "@/components/bs-ui/button"
import { Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle } from "@/components/bs-ui/dialog"
import { message } from "@/components/bs-ui/toast/use-toast"
import { mutateResourceGrantsApi } from "@/controllers/API/serviceAccount"
import { captureAndAlertRequestErrorHoc } from "@/controllers/request"
import { ResourceGrant, ResourceGrantLevel } from "@/types/api/serviceAccount"
import { useEffect, useState } from "react"
import { useTranslation } from "react-i18next"
import { GrantLevelSelect } from "./GrantLevelSelect"

interface ResourceGrantLevelDialogProps {
    open: boolean
    accountId: number
    /** The direct grant being edited; null while closed. */
    grant: ResourceGrant | null
    /** Resolved resource name, or the raw id when the lookup has not resolved it. */
    displayName: string
    onClose: (open: boolean) => void
    onUpdated: () => void
}

export function ResourceGrantLevelDialog({
    open,
    accountId,
    grant,
    displayName,
    onClose,
    onUpdated,
}: ResourceGrantLevelDialogProps) {
    const { t } = useTranslation("serviceAccount")
    const [level, setLevel] = useState<ResourceGrantLevel>("viewer")
    const [submitting, setSubmitting] = useState(false)

    useEffect(() => {
        if (!open || !grant) return
        setLevel(grant.level ?? "viewer")
        setSubmitting(false)
    }, [open, grant])

    const handleSave = () => {
        if (!grant) return
        setSubmitting(true)
        captureAndAlertRequestErrorHoc(
            mutateResourceGrantsApi(accountId, {
                grants: [
                    {
                        resource_type: grant.resource_type,
                        resource_id: grant.resource_id,
                        level,
                    },
                ],
            }),
        )
            .then((res) => {
                if (!res) {
                    // On a partial failure the table may differ from what is on screen;
                    // refresh so the admin sees the actual state and can retry.
                    onUpdated()
                    return
                }
                message({ title: t("prompt"), description: t("grants.levelUpdateSuccess"), variant: "success" })
                onClose(false)
                onUpdated()
            })
            .finally(() => setSubmitting(false))
    }

    return (
        <Dialog
            open={open}
            onOpenChange={(next) => {
                if (next || submitting) return
                onClose(false)
            }}
        >
            <DialogContent className="sm:max-w-[520px]" close={!submitting}>
                <DialogHeader>
                    <DialogTitle>{t("grants.levelDialog.title")}</DialogTitle>
                </DialogHeader>
                {grant && (
                    <div className="flex flex-col gap-5">
                        <p className="text-sm text-muted-foreground">
                            {t("grants.levelDialog.desc", {
                                name: displayName || grant.resource_id,
                                type: t(`grants.type.${grant.resource_type}`),
                            })}
                        </p>
                        <GrantLevelSelect value={level} onChange={setLevel} disabled={submitting} />
                        <p className="text-xs text-muted-foreground">{t(`grants.levelHint.${level}`)}</p>
                    </div>
                )}
                <DialogFooter className="mt-4">
                    <Button
                        variant="outline"
                        className="h-10 w-[120px] px-16"
                        disabled={submitting}
                        onClick={() => onClose(false)}
                    >
                        {t("cancel")}
                    </Button>
                    <Button className="h-10 w-[120px] px-16" disabled={submitting || !grant} onClick={handleSave}>
                        {t("grants.levelDialog.save")}
                    </Button>
                </DialogFooter>
            </DialogContent>
        </Dialog>
    )
}
