import { bsConfirm } from "@/components/bs-ui/alertDialog/useConfirm"
import { Button } from "@/components/bs-ui/button"
import { Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle } from "@/components/bs-ui/dialog"
import { Input } from "@/components/bs-ui/input"
import { Label } from "@/components/bs-ui/label"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/bs-ui/select"
import { createTaxonomyApi, deleteTaxonomyApi, listTaxonomiesApi } from "@/controllers/API/managedAgent"
import { captureAndAlertRequestErrorHoc } from "@/controllers/request"
import type { ManagedAgentTaxonomy } from "@/types/api/managedAgent"
import { useEffect, useState } from "react"
import { useTranslation } from "react-i18next"

interface Props {
    open: boolean
    onClose: () => void
}

const EMPTY = { kind: "scenario" as "scenario" | "industry", code: "", name_zh: "", name_en: "", name_ja: "" }

export function TaxonomyDialog({ open, onClose }: Props) {
    const { t } = useTranslation("managedAgent")
    const [rows, setRows] = useState<ManagedAgentTaxonomy[]>([])
    const [form, setForm] = useState(EMPTY)
    const [saving, setSaving] = useState(false)

    const load = () => {
        captureAndAlertRequestErrorHoc(listTaxonomiesApi()).then((res) => setRows(res || []))
    }

    useEffect(() => {
        if (open) load()
    }, [open])

    const create = async () => {
        setSaving(true)
        try {
            await captureAndAlertRequestErrorHoc(
                createTaxonomyApi({
                    kind: form.kind,
                    code: form.code.trim(),
                    name_zh: form.name_zh.trim(),
                    name_en: form.name_en.trim() || undefined,
                    name_ja: form.name_ja.trim() || undefined,
                }),
            )
            setForm(EMPTY)
            load()
        } finally {
            setSaving(false)
        }
    }

    const remove = (row: ManagedAgentTaxonomy) => {
        bsConfirm({
            title: t("taxonomy.deleteTitle"),
            desc: t("taxonomy.deleteDesc", { name: row.name_zh }),
            okTxt: t("common.confirm"),
            canelTxt: t("common.cancel"),
            onOk: async () => {
                await captureAndAlertRequestErrorHoc(deleteTaxonomyApi(row.id))
                load()
            },
        })
    }

    return (
        <Dialog open={open} onOpenChange={(next) => !next && onClose()}>
            <DialogContent className="max-w-3xl max-h-[85vh] overflow-y-auto">
                <DialogHeader>
                    <DialogTitle>{t("taxonomy.title")}</DialogTitle>
                </DialogHeader>
                <div className="grid grid-cols-5 gap-3 items-end py-2">
                    <div>
                        <Label>{t("taxonomy.kind")}</Label>
                        <Select value={form.kind} onValueChange={(v) => setForm({ ...form, kind: v as typeof form.kind })}>
                            <SelectTrigger><SelectValue /></SelectTrigger>
                            <SelectContent>
                                <SelectItem value="scenario">{t("taxonomy.scenario")}</SelectItem>
                                <SelectItem value="industry">{t("taxonomy.industry")}</SelectItem>
                            </SelectContent>
                        </Select>
                    </div>
                    <div><Label>{t("taxonomy.code")}</Label><Input value={form.code} onChange={(e) => setForm({ ...form, code: e.target.value })} /></div>
                    <div><Label>{t("taxonomy.nameZh")}</Label><Input value={form.name_zh} onChange={(e) => setForm({ ...form, name_zh: e.target.value })} /></div>
                    <div><Label>{t("taxonomy.nameEn")}</Label><Input value={form.name_en} onChange={(e) => setForm({ ...form, name_en: e.target.value })} /></div>
                    <div><Label>{t("taxonomy.nameJa")}</Label><Input value={form.name_ja} onChange={(e) => setForm({ ...form, name_ja: e.target.value })} /></div>
                </div>
                <Button className="mb-4" disabled={saving || !form.code.trim() || !form.name_zh.trim()} onClick={create}>
                    {t("taxonomy.add")}
                </Button>
                <div className="space-y-2">
                    {rows.map((row) => (
                        <div key={row.id} className="flex items-center justify-between rounded border px-3 py-2">
                            <div className="text-sm">
                                <span className="mr-2 text-muted-foreground">{row.kind === "scenario" ? t("taxonomy.scenario") : t("taxonomy.industry")}</span>
                                <span className="font-mono mr-2">{row.code}</span>
                                <span>{row.name_zh}</span>
                                {!row.is_enabled && <span className="ml-2 text-xs text-muted-foreground">({t("taxonomy.disabled")})</span>}
                            </div>
                            <Button variant="ghost" size="sm" onClick={() => remove(row)}>{t("common.delete")}</Button>
                        </div>
                    ))}
                </div>
                <DialogFooter>
                    <Button variant="outline" onClick={onClose}>{t("common.close")}</Button>
                </DialogFooter>
            </DialogContent>
        </Dialog>
    )
}
