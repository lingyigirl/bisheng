import { Button } from "@/components/bs-ui/button"
import { Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle } from "@/components/bs-ui/dialog"
import { Input } from "@/components/bs-ui/input"
import { Label } from "@/components/bs-ui/label"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/bs-ui/select"
import { message } from "@/components/bs-ui/toast/use-toast"
import {
    createScheduledTaskApi,
    draftScheduledTaskApi,
    listAvailableIdentitiesApi,
    previewCronApi,
    updateScheduledTaskApi,
} from "@/controllers/API/scheduledTask"
import { captureAndAlertRequestErrorHoc } from "@/controllers/request"
import {
    AvailableIdentityItem,
    CronPreviewResp,
    PresetInputItem,
    ScheduleType,
    ScheduledTaskItem,
} from "@/types/api/scheduledTask"
import { formatIsoDateTime } from "@/util/utils"
import { useEffect, useState } from "react"
import { useTranslation } from "react-i18next"
import { formatScheduleDisplay } from "./formatScheduleDisplay"

interface TaskFormDialogProps {
    open: boolean
    onClose: (open: boolean) => void
    appId: string
    appType: number
    /** 传入即为编辑（自然语言只用于新建，AC-43）。 */
    task?: ScheduledTaskItem | null
    onSaved: () => void
}

/** `datetime-local` 输入是 `YYYY-MM-DDTHH:mm`；后端列是 naive 本地时间（秒级）。 */
function toNaiveIso(value: string): string {
    if (!value) return ""
    return value.length === 16 ? `${value}:00` : value
}

function toLocalInput(value: string | null): string {
    if (!value) return ""
    return value.slice(0, 16)
}

/**
 * 创建 / 编辑定时任务。自然语言只出现在新建模式：输入一句话 → 生成**不落库**的草稿
 * → 预填本表单 → 用户确认保存才真正创建（AC-42）。
 */
export function TaskFormDialog({ open, onClose, appId, appType, task, onSaved }: TaskFormDialogProps) {
    const { t, i18n } = useTranslation("scheduledTask")
    const isEdit = !!task

    const [name, setName] = useState("")
    const [description, setDescription] = useState("")
    const [scheduleType, setScheduleType] = useState<ScheduleType>("cron")
    const [cron, setCron] = useState("")
    const [runAt, setRunAt] = useState("")
    const [identityId, setIdentityId] = useState<string>("")
    const [presets, setPresets] = useState<PresetInputItem[]>([])
    const [identities, setIdentities] = useState<AvailableIdentityItem[]>([])
    const [preview, setPreview] = useState<CronPreviewResp | null>(null)
    const [nlText, setNlText] = useState("")
    const [nlRationale, setNlRationale] = useState("")
    const [nlBusy, setNlBusy] = useState(false)
    const [saving, setSaving] = useState(false)

    useEffect(() => {
        if (!open) return
        setName(task?.name ?? "")
        setDescription(task?.description ?? "")
        setScheduleType(task?.schedule_type ?? "cron")
        setCron(task?.cron_expression ?? "")
        setRunAt(toLocalInput(task?.run_at ?? null))
        setIdentityId(task ? String(task.service_account_id) : "")
        setPresets([])
        setPreview(null)
        setNlText("")
        setNlRationale("")
        captureAndAlertRequestErrorHoc(listAvailableIdentitiesApi({ app_id: appId, app_type: appType })).then(
            (res) => res && setIdentities(res.data || []),
        )
    }, [open, task, appId, appType])

    const handlePreview = () => {
        if (!cron.trim()) return
        captureAndAlertRequestErrorHoc(previewCronApi(cron.trim())).then((res) => res && setPreview(res))
    }

    const handleDraft = () => {
        if (!nlText.trim()) return
        setNlBusy(true)
        captureAndAlertRequestErrorHoc(
            draftScheduledTaskApi({ app_id: appId, app_type: appType, text: nlText.trim() }),
        )
            .then((res) => {
                if (!res) return
                setScheduleType(res.schedule_type)
                setCron(res.cron_expression ?? "")
                setRunAt(toLocalInput(res.run_at))
                setPresets(res.preset_inputs ?? [])
                setNlRationale(res.rationale)
                setPreview({
                    expression: res.cron_expression ?? (res.run_at ?? ""),
                    description: res.schedule_description,
                    schedule_display: res.schedule_display,
                    next_runs: res.next_runs,
                })
            })
            .finally(() => setNlBusy(false))
    }

    const handleSave = () => {
        const body = {
            name: name.trim(),
            description: description.trim() || null,
            schedule_type: scheduleType,
            cron_expression: scheduleType === "cron" ? cron.trim() : null,
            run_at: scheduleType === "once" ? toNaiveIso(runAt) : null,
            service_account_id: Number(identityId),
            preset_inputs: presets.filter((item) => item.key.trim()).map((item) => ({
                key: item.key.trim(),
                value: item.value,
            })),
        }
        if (!body.name || !body.service_account_id) return
        setSaving(true)
        const request = isEdit && task ? updateScheduledTaskApi(task.id, body) : createScheduledTaskApi({ ...body, app_id: appId, app_type: appType })
        captureAndAlertRequestErrorHoc(request)
            .then((res) => {
                if (!res) return
                message({ title: t("prompt"), description: t(isEdit ? "saveSuccess" : "createSuccess"), variant: "success" })
                onSaved()
                onClose(false)
            })
            .finally(() => setSaving(false))
    }

    const addPreset = () => setPresets([...presets, { key: "", value: "" }])
    const updatePreset = (index: number, patch: Partial<PresetInputItem>) =>
        setPresets(presets.map((item, i) => (i === index ? { ...item, ...patch } : item)))
    const removePreset = (index: number) => setPresets(presets.filter((_, i) => i !== index))

    return (
        <Dialog open={open} onOpenChange={onClose}>
            <DialogContent className="max-h-[86vh] overflow-y-auto sm:max-w-[620px]">
                <DialogHeader>
                    <DialogTitle>{t(isEdit ? "form.editTitle" : "form.createTitle")}</DialogTitle>
                </DialogHeader>

                <div className="flex flex-col gap-4">
                    {/* 自然语言只用于新建（AC-43） */}
                    {!isEdit && (
                        <div className="rounded-lg border p-3">
                            <Label className="mb-2 block">{t("form.nlLabel")}</Label>
                            <textarea
                                className="min-h-[64px] w-full rounded-md border bg-transparent p-2 text-sm outline-none"
                                placeholder={t("form.nlPlaceholder")}
                                maxLength={2000}
                                value={nlText}
                                onChange={(e) => setNlText(e.target.value)}
                            />
                            <div className="mt-2 flex items-center justify-between">
                                <span className="text-xs text-muted-foreground">{t("form.nlHint")}</span>
                                <Button variant="outline" size="sm" disabled={nlBusy || !nlText.trim()} onClick={handleDraft}>
                                    {nlBusy ? t("form.drafting") : t("form.generateDraft")}
                                </Button>
                            </div>
                            {nlRationale && <p className="mt-2 text-xs text-muted-foreground">{nlRationale}</p>}
                        </div>
                    )}

                    <div className="flex flex-col gap-1">
                        <Label>{t("form.name")}</Label>
                        <Input maxLength={128} value={name} onChange={(e) => setName(e.target.value)} />
                    </div>

                    <div className="flex flex-col gap-1">
                        <Label>{t("form.description")}</Label>
                        <Input maxLength={512} value={description} onChange={(e) => setDescription(e.target.value)} />
                    </div>

                    <div className="flex flex-col gap-1">
                        <Label>{t("form.scheduleType")}</Label>
                        <Select value={scheduleType} onValueChange={(value) => setScheduleType(value as ScheduleType)}>
                            <SelectTrigger><SelectValue /></SelectTrigger>
                            <SelectContent>
                                <SelectItem value="cron">{t("form.typeCron")}</SelectItem>
                                <SelectItem value="once">{t("form.typeOnce")}</SelectItem>
                            </SelectContent>
                        </Select>
                    </div>

                    {scheduleType === "cron" ? (
                        <div className="flex flex-col gap-1">
                            <Label>{t("form.cron")}</Label>
                            <div className="flex gap-2">
                                <Input
                                    className="font-mono"
                                    placeholder="0 9 * * 1-5"
                                    value={cron}
                                    onChange={(e) => {
                                        setCron(e.target.value)
                                        setPreview(null)
                                    }}
                                />
                                <Button variant="outline" onClick={handlePreview} disabled={!cron.trim()}>
                                    {t("form.preview")}
                                </Button>
                            </div>
                            {preview && (
                                <div className="text-xs text-muted-foreground">
                                    <p>
                                        {formatScheduleDisplay(
                                            t,
                                            i18n.language,
                                            preview.schedule_display,
                                            preview.description,
                                        )}
                                    </p>
                                    {preview.next_runs.length > 0 && (
                                        <p className="mt-1">
                                            {t("form.nextRuns")}:{" "}
                                            {preview.next_runs
                                                .slice(0, 5)
                                                .map((item) => formatIsoDateTime(item))
                                                .join(" · ")}
                                        </p>
                                    )}
                                </div>
                            )}
                        </div>
                    ) : (
                        <div className="flex flex-col gap-1">
                            <Label>{t("form.runAt")}</Label>
                            <Input type="datetime-local" value={runAt} onChange={(e) => setRunAt(e.target.value)} />
                            <span className="text-xs text-muted-foreground">{t("form.timezone")}</span>
                        </div>
                    )}

                    <div className="flex flex-col gap-1">
                        <Label>{t("form.identity")}</Label>
                        <Select value={identityId} onValueChange={setIdentityId}>
                            <SelectTrigger><SelectValue placeholder={t("form.identityPlaceholder")} /></SelectTrigger>
                            <SelectContent>
                                {identities.map((item) => (
                                    <SelectItem key={item.service_account_id} value={String(item.service_account_id)} disabled={!item.usable}>
                                        {item.name}
                                        {!item.usable ? ` · ${t(item.enabled ? "form.noUseApp" : "form.accountDisabled")}` : ""}
                                    </SelectItem>
                                ))}
                            </SelectContent>
                        </Select>
                        <span className="text-xs text-muted-foreground">{t("form.identityHint")}</span>
                    </div>

                    <div className="flex flex-col gap-2">
                        <div className="flex items-center justify-between">
                            <Label>{t("form.presets")}</Label>
                            <Button variant="link" size="sm" onClick={addPreset}>{t("form.addPreset")}</Button>
                        </div>
                        {presets.map((item, index) => (
                            <div key={index} className="flex gap-2">
                                <Input
                                    className="w-1/3"
                                    placeholder={t("form.presetKey")}
                                    value={item.key}
                                    onChange={(e) => updatePreset(index, { key: e.target.value })}
                                />
                                <Input
                                    placeholder={t("form.presetValue")}
                                    value={item.value}
                                    onChange={(e) => updatePreset(index, { value: e.target.value })}
                                />
                                <Button variant="link" size="sm" className="text-red-500" onClick={() => removePreset(index)}>
                                    {t("delete")}
                                </Button>
                            </div>
                        ))}
                        {/* AC-48：助手/工作流定时运行都不产生会话与消息，结果看任务列表与监控。 */}
                        <span className="text-xs text-muted-foreground">{t("form.noSessionHint")}</span>
                    </div>
                </div>

                <DialogFooter className="mt-4">
                    <Button variant="outline" className="h-10 px-6" onClick={() => onClose(false)}>
                        {t("cancel")}
                    </Button>
                    <Button className="h-10 px-8" disabled={saving || !name.trim() || !identityId} onClick={handleSave}>
                        {saving ? t("saving") : t("save")}
                    </Button>
                </DialogFooter>
            </DialogContent>
        </Dialog>
    )
}
