import { bsConfirm } from "@/components/bs-ui/alertDialog/useConfirm"
import { Button } from "@/components/bs-ui/button"
import { Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle } from "@/components/bs-ui/dialog"
import { Input, Textarea } from "@/components/bs-ui/input"
import { Label } from "@/components/bs-ui/label"
import { Switch } from "@/components/bs-ui/switch"
import { message } from "@/components/bs-ui/toast/use-toast"
import {
    deleteServiceAccountApi,
    disableServiceAccountApi,
    enableServiceAccountApi,
    updateServiceAccountApi,
} from "@/controllers/API/serviceAccount"
import { captureAndAlertRequestErrorHoc } from "@/controllers/request"
import { ServiceAccount, ServiceAccountUpdate } from "@/types/api/serviceAccount"
import { formatIsoDateTime } from "@/util/utils"
import { ReactNode, useEffect, useState } from "react"
import { useTranslation } from "react-i18next"

interface InfoRowProps {
    label: string
    children: ReactNode
}

function InfoRow({ label, children }: InfoRowProps) {
    return (
        <div className="flex items-start gap-6">
            <span className="w-[110px] shrink-0 pt-[2px] text-sm text-muted-foreground">{label}</span>
            <div className="flex min-w-0 flex-1 flex-col gap-1 text-sm">{children}</div>
        </div>
    )
}

interface EditAccountDialogProps {
    account: ServiceAccount
    open: boolean
    onClose: (open: boolean) => void
    onSaved: () => void
}

function EditAccountDialog({ account, open, onClose, onSaved }: EditAccountDialogProps) {
    const { t } = useTranslation("serviceAccount")
    const [name, setName] = useState(account.name)
    const [description, setDescription] = useState(account.description ?? "")
    const [submitting, setSubmitting] = useState(false)

    // Stays mounted across openings, so re-seed every time it opens.
    useEffect(() => {
        if (!open) return
        setName(account.name)
        setDescription(account.description ?? "")
    }, [open, account])

    const handleConfirm = () => {
        const nextName = name.trim()
        if (!nextName) {
            message({ title: t("prompt"), description: t("form.nameRequired"), variant: "warning" })
            return
        }
        // Send only what changed: the endpoint applies `exclude_unset`, and an
        // explicit null goes straight into NOT NULL columns (`name`).
        const payload: ServiceAccountUpdate = {}
        if (nextName !== account.name) payload.name = nextName
        const nextDescription = description.trim()
        if (nextDescription !== (account.description ?? "")) payload.description = nextDescription || null
        if (!Object.keys(payload).length) {
            onClose(false)
            return
        }

        setSubmitting(true)
        captureAndAlertRequestErrorHoc(updateServiceAccountApi(account.id, payload))
            .then((res) => {
                if (!res) return
                message({ title: t("prompt"), description: t("detail.saveSuccess"), variant: "success" })
                onClose(false)
                onSaved()
            })
            .finally(() => setSubmitting(false))
    }

    return (
        <Dialog open={open} onOpenChange={(next) => !next && onClose(false)}>
            <DialogContent className="sm:max-w-[560px]">
                <DialogHeader>
                    <DialogTitle>{t("detail.editTitle")}</DialogTitle>
                </DialogHeader>
                <div className="flex flex-col gap-6">
                    <div>
                        <Label className="bisheng-label">{t("detail.name")}</Label>
                        <Input
                            value={name}
                            maxLength={128}
                            placeholder={t("form.namePlaceholder")}
                            onChange={(e) => setName(e.target.value)}
                        />
                    </div>
                    <div>
                        <Label className="bisheng-label">{t("detail.description")}</Label>
                        <Textarea
                            value={description}
                            maxLength={512}
                            placeholder={t("form.descriptionPlaceholder")}
                            onChange={(e) => setDescription(e.target.value)}
                        />
                    </div>
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

interface OverviewTabProps {
    account: ServiceAccount
    /** Writes answer with stale stats; the parent re-reads the account instead. */
    onChanged: () => Promise<void>
    /** Account deleted: the parent returns to the list. */
    onDeleted: () => void
}

export function OverviewTab({ account, onChanged, onDeleted }: OverviewTabProps) {
    const { t } = useTranslation("serviceAccount")
    const [editOpen, setEditOpen] = useState(false)

    const handleToggleEnabled = (nextEnabled: boolean) => {
        const run = () =>
            captureAndAlertRequestErrorHoc(
                (nextEnabled ? enableServiceAccountApi : disableServiceAccountApi)(account.id),
            ).then((res) => {
                if (!res) return
                message({
                    title: t("prompt"),
                    description: nextEnabled ? t("enableSuccess") : t("disableSuccess"),
                    variant: "success",
                })
                void onChanged()
            })

        if (nextEnabled) {
            void run()
            return
        }
        // Disabling revokes every key at once, and re-enabling does not bring
        // them back -- always spell that out before doing it.
        bsConfirm({
            title: t("prompt"),
            desc: t("confirm.disableDesc", { name: account.name, count: account.key_count }),
            okTxt: t("confirm.disableOk"),
            onOk(next) {
                void run().then(() => next())
            },
        })
    }

    const handleDelete = () => {
        bsConfirm({
            title: t("prompt"),
            desc: t("confirm.deleteDesc", { name: account.name }),
            okTxt: t("delete"),
            onOk(next) {
                captureAndAlertRequestErrorHoc(deleteServiceAccountApi(account.id)).then((res) => {
                    if (res) {
                        message({ title: t("prompt"), description: t("deleteSuccess"), variant: "success" })
                        // Leave the detail view first: the account behind it is gone.
                        onDeleted()
                    }
                    next()
                })
            },
        })
    }

    return (
        <div className="flex max-w-[760px] flex-col gap-6 pb-4">
            <div className="flex flex-col gap-4">
                <InfoRow label={t("detail.name")}>
                    <div className="flex items-center gap-2">
                        <span>{account.name}</span>
                        <Button variant="link" size="sm" className="h-6 px-0" onClick={() => setEditOpen(true)}>
                            {t("detail.edit")}
                        </Button>
                    </div>
                </InfoRow>

                <InfoRow label={t("detail.description")}>
                    <span className={account.description ? undefined : "text-muted-foreground"}>
                        {account.description || t("detail.noDescription")}
                    </span>
                </InfoRow>

                <InfoRow label={t("detail.status")}>
                    <div className="flex items-center gap-3">
                        <Switch checked={account.enabled} onCheckedChange={handleToggleEnabled} />
                        <span className="text-muted-foreground">
                            {account.enabled ? t("detail.enabledHint") : t("detail.disabledHint")}
                        </span>
                    </div>
                </InfoRow>

                <InfoRow label={t("detail.owner")}>
                    <span className="font-mono">{t("detail.ownerValue", { id: account.resource_owner_user_id })}</span>
                    <span className="text-xs text-muted-foreground">{t("detail.ownerHint")}</span>
                </InfoRow>

                <InfoRow label={t("detail.keyCount")}>
                    <span className={account.key_count === 0 ? "text-red-500" : undefined}>{account.key_count}</span>
                </InfoRow>

                <InfoRow label={t("detail.lastUsed")}>
                    <span className="text-muted-foreground">
                        {account.last_used_at ? formatIsoDateTime(account.last_used_at) : t("never")}
                    </span>
                </InfoRow>
            </div>

            <div className="flex flex-col gap-2 rounded-lg border border-red-200 p-4">
                <span className="text-sm font-medium">{t("detail.dangerZone")}</span>
                <span className="text-xs text-muted-foreground">{t("detail.dangerHint")}</span>
                <div>
                    <Button
                        variant="outline"
                        className="mt-2 border-red-200 px-6 text-red-500"
                        onClick={handleDelete}
                    >
                        {t("delete")}
                    </Button>
                </div>
            </div>

            <EditAccountDialog account={account} open={editOpen} onClose={setEditOpen} onSaved={() => void onChanged()} />
        </div>
    )
}
