import { Button } from "@/components/bs-ui/button"
import { Checkbox } from "@/components/bs-ui/checkBox"
import { Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle } from "@/components/bs-ui/dialog"
import { SearchInput } from "@/components/bs-ui/input"
import { Label } from "@/components/bs-ui/label"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/bs-ui/select"
import { message } from "@/components/bs-ui/toast/use-toast"
import { mutateResourceGrantsApi } from "@/controllers/API/serviceAccount"
import { captureAndAlertRequestErrorHoc } from "@/controllers/request"
import { ResourceGrantItemInput, ResourceGrantLevel, ResourceGrantType } from "@/types/api/serviceAccount"
import { useCallback, useEffect, useRef, useState } from "react"
import { useTranslation } from "react-i18next"
import { GrantLevelSelect } from "./GrantLevelSelect"
import { PICKER_PAGE_SIZE, PickerItem, RESOURCE_LOADERS, RESOURCE_TYPES } from "./resourceGrantLoaders"

interface ResourceGrantDialogProps {
    open: boolean
    accountId: number
    onClose: (open: boolean) => void
    onGranted: () => void
}

export function ResourceGrantDialog({ open, accountId, onClose, onGranted }: ResourceGrantDialogProps) {
    const { t } = useTranslation("serviceAccount")
    const [resourceType, setResourceType] = useState<ResourceGrantType>("knowledge_library")
    const [keyword, setKeyword] = useState("")
    const [options, setOptions] = useState<PickerItem[]>([])
    const [selected, setSelected] = useState<string[]>([])
    const [level, setLevel] = useState<ResourceGrantLevel>("viewer")
    const [loading, setLoading] = useState(false)
    const [submitting, setSubmitting] = useState(false)

    // Guards against a slow response from the previous type landing last.
    const searchSeq = useRef(0)
    const search = useCallback(async (type: ResourceGrantType, name: string) => {
        const seq = ++searchSeq.current
        setLoading(true)
        const res = await captureAndAlertRequestErrorHoc(RESOURCE_LOADERS[type](name, PICKER_PAGE_SIZE))
        if (seq !== searchSeq.current) return
        setOptions(res || [])
        setLoading(false)
    }, [])

    // Fresh form on every open; re-search whenever the type changes.
    useEffect(() => {
        if (!open) return
        setKeyword("")
        setSelected([])
        setLevel("viewer")
        void search(resourceType, "")
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [open])

    const handleTypeChange = (next: ResourceGrantType) => {
        setResourceType(next)
        setSelected([])
        // Clear first: while the new type loads, the previous type's rows must not be
        // clickable (they would be submitted under the new resource type).
        setOptions([])
        void search(next, keyword)
    }

    const handleToggle = (id: string) => {
        setSelected((prev) => (prev.includes(id) ? prev.filter((one) => one !== id) : [...prev, id]))
    }

    // Select-all is scoped to the current search result (the picker loads at most
    // PICKER_PAGE_SIZE items and has no load-more). Selections hidden by a later
    // search are preserved: deselect-all only removes the currently visible ids.
    const visibleIds = options.map((option) => option.id)
    const allVisibleSelected = visibleIds.length > 0 && visibleIds.every((id) => selected.includes(id))
    const handleToggleAll = () => {
        if (!visibleIds.length) return
        setSelected((prev) =>
            allVisibleSelected
                ? prev.filter((id) => !visibleIds.includes(id))
                : [...prev, ...visibleIds.filter((id) => !prev.includes(id))],
        )
    }

    const handleConfirm = () => {
        if (!selected.length) {
            message({ title: t("prompt"), description: t("grants.selectRequired"), variant: "warning" })
            return
        }
        const grants: ResourceGrantItemInput[] = selected.map((id) => ({
            resource_type: resourceType,
            resource_id: id,
            level,
        }))
        setSubmitting(true)
        captureAndAlertRequestErrorHoc(mutateResourceGrantsApi(accountId, { grants }))
            .then((res) => {
                if (!res) return
                message({
                    title: t("prompt"),
                    description: t("grants.grantSuccess", { count: res.granted }),
                    variant: "success",
                })
                onClose(false)
                onGranted()
            })
            .finally(() => setSubmitting(false))
    }

    return (
        <Dialog
            open={open}
            onOpenChange={(next) => {
                if (next) return
                // The mutate is already on the wire; letting the form vanish now would
                // leave the admin believing a submitted grant was cancelled.
                if (submitting) return
                onClose(false)
            }}
        >
            <DialogContent className="sm:max-w-[640px]" close={!submitting}>
                <DialogHeader>
                    <DialogTitle>{t("grants.dialog.title")}</DialogTitle>
                </DialogHeader>
                <div className="flex flex-col gap-5">
                    <div className="flex items-end gap-4">
                        <div className="w-[220px]">
                            <Label className="bisheng-label">{t("grants.dialog.type")}</Label>
                            <Select value={resourceType} onValueChange={(v) => handleTypeChange(v as ResourceGrantType)}>
                                <SelectTrigger>
                                    <SelectValue />
                                </SelectTrigger>
                                <SelectContent>
                                    {RESOURCE_TYPES.map((type) => (
                                        <SelectItem key={type} value={type}>
                                            {t(`grants.type.${type}`)}
                                        </SelectItem>
                                    ))}
                                </SelectContent>
                            </Select>
                        </div>
                        <GrantLevelSelect className="w-[220px]" value={level} onChange={setLevel} disabled={submitting} />
                    </div>
                    {/* One level for the whole batch: the mutate endpoint takes it per
                        item, and "these N resources at this level" is the only shape
                        that stays comprehensible in one dialog. */}
                    <p className="text-xs text-muted-foreground">{t(`grants.levelHint.${level}`)}</p>

                    <div className="flex flex-col gap-2">
                        <Label className="bisheng-label">{t("grants.dialog.resource")}</Label>
                        <SearchInput
                            placeholder={t("grants.dialog.searchPlaceholder")}
                            value={keyword}
                            onChange={(e) => setKeyword(e.target.value)}
                            onKeyDown={(e) => {
                                if (e.key === "Enter") void search(resourceType, keyword)
                            }}
                        />
                        <div className="rounded-md border">
                            <div className="flex items-center gap-2 border-b px-2 py-1.5">
                                <Checkbox
                                    checked={allVisibleSelected}
                                    disabled={loading || options.length === 0}
                                    aria-label={
                                        allVisibleSelected
                                            ? t("grants.dialog.deselectAll")
                                            : t("grants.dialog.selectAll")
                                    }
                                    onCheckedChange={handleToggleAll}
                                />
                                <span className="text-sm">
                                    {allVisibleSelected
                                        ? t("grants.dialog.deselectAll")
                                        : t("grants.dialog.selectAll")}
                                </span>
                            </div>
                            <div className="max-h-[260px] min-h-[120px] overflow-y-auto p-2">
                                {loading ? (
                                    <p className="p-3 text-center text-sm text-muted-foreground">{t("loading")}</p>
                                ) : options.length === 0 ? (
                                    <p className="p-3 text-center text-sm text-muted-foreground">
                                        {t("grants.dialog.empty")}
                                    </p>
                                ) : (
                                    options.map((option) => (
                                        <label
                                            key={option.id}
                                            className="flex cursor-pointer items-center gap-2 rounded px-2 py-1.5 text-sm hover:bg-accent"
                                        >
                                            <Checkbox
                                                checked={selected.includes(option.id)}
                                                onCheckedChange={() => handleToggle(option.id)}
                                            />
                                            <span className="min-w-0 flex-1 truncate">{option.name}</span>
                                            <code className="shrink-0 text-xs text-muted-foreground">
                                                {option.id}
                                            </code>
                                        </label>
                                    ))
                                )}
                            </div>
                        </div>
                        <p className="text-xs text-muted-foreground">
                            {t("grants.dialog.selected", { count: selected.length })}
                        </p>
                        <p className="text-xs text-muted-foreground">{t("grants.dialog.adminOnlyHint")}</p>
                    </div>
                </div>
                <DialogFooter className="mt-4">
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
