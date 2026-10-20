// @ts-strict-ignore
import { Button } from "@/components/bs-ui/button"
import { Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle } from "@/components/bs-ui/dialog"
import { Input } from "@/components/bs-ui/input"
import { Label } from "@/components/bs-ui/label"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/bs-ui/select"
import { listLLMServers, type LLMModelInfo } from "@/controllers/API/llm"
import {
    type KnowledgeGraphBuildPayload,
} from "@/controllers/API/knowledgeGraph"
import { useEffect, useMemo, useState } from "react"
import { useTranslation } from "react-i18next"

interface ModelOption {
    id: number
    label: string
}

interface BuildGraphDialogProps {
    open: boolean
    onOpenChange: (open: boolean) => void
    isRebuild: boolean
    loading?: boolean
    onSubmit: (payload: KnowledgeGraphBuildPayload) => void
}

export function BuildGraphDialog({
    open,
    onOpenChange,
    isRebuild,
    loading = false,
    onSubmit,
}: BuildGraphDialogProps) {
    const { t } = useTranslation("knowledge")
    const defaultEntityTypes = t("knowledgeGraph.entityTypesDefault")
    const [models, setModels] = useState<ModelOption[]>([])
    const [modelId, setModelId] = useState("")
    const [typesText, setTypesText] = useState(defaultEntityTypes)

    useEffect(() => {
        if (!open) return
        setModelId("")
        setTypesText(defaultEntityTypes)
        listLLMServers()
            .then((servers) => {
                const options: ModelOption[] = []
                servers.forEach((server) => {
                    server.models?.forEach((model: LLMModelInfo) => {
                        if (model.model_type === "llm" && model.online !== false) {
                            options.push({
                                id: model.id,
                                label: `${server.name} / ${model.name}`,
                            })
                        }
                    })
                })
                setModels(options)
            })
            .catch(() => setModels([]))
    }, [open, defaultEntityTypes])

    const entityTypes = useMemo(
        () => typesText.split(/[,，、\n]/).map((item) => item.trim()).filter(Boolean),
        [typesText],
    )

    const handleSubmit = () => {
        onSubmit({
            model_id: modelId ? Number(modelId) : null,
            entity_types: entityTypes.length ? entityTypes : null,
            force: isRebuild,
        })
    }

    return (
        <Dialog open={open} onOpenChange={onOpenChange}>
            <DialogContent className="sm:max-w-[560px] bg-background-login">
                <DialogHeader>
                    <DialogTitle>
                        {isRebuild ? t("knowledgeGraph.rebuildTitle") : t("knowledgeGraph.buildTitle")}
                    </DialogTitle>
                </DialogHeader>
                <div className="flex flex-col gap-4">
                    <div className="rounded-[6px] bg-[#fff7e6] px-3 py-2 text-[12px] leading-5 text-[#d46b08]">
                        {isRebuild ? t("knowledgeGraph.rebuildWarning") : t("knowledgeGraph.buildWarning")}
                    </div>
                    <div>
                        <Label className="bisheng-label">{t("knowledgeGraph.model")}</Label>
                        <Select value={modelId || "__default__"} onValueChange={(value) => setModelId(value === "__default__" ? "" : value)}>
                            <SelectTrigger className="mt-1">
                                <SelectValue placeholder={t("knowledgeGraph.modelPlaceholder")} />
                            </SelectTrigger>
                            <SelectContent>
                                <SelectItem value="__default__">{t("knowledgeGraph.useDefaultModel")}</SelectItem>
                                {models.map((model) => (
                                    <SelectItem key={model.id} value={String(model.id)}>
                                        {model.label}
                                    </SelectItem>
                                ))}
                            </SelectContent>
                        </Select>
                    </div>
                    <div>
                        <Label className="bisheng-label">{t("knowledgeGraph.entityTypes")}</Label>
                        <Input
                            className="mt-1"
                            value={typesText}
                            onChange={(event) => setTypesText(event.target.value)}
                            placeholder={t("knowledgeGraph.entityTypesPlaceholder")}
                        />
                        <p className="mt-1 text-[12px] text-[#86909c]">{t("knowledgeGraph.entityTypesHint")}</p>
                    </div>
                </div>
                <DialogFooter>
                    <Button variant="outline" onClick={() => onOpenChange(false)} disabled={loading}>
                        {t("knowledgeGraph.cancel")}
                    </Button>
                    <Button onClick={handleSubmit} disabled={loading || !typesText.trim()}>
                        {loading ? t("knowledgeGraph.submitting") : t("knowledgeGraph.startBuild")}
                    </Button>
                </DialogFooter>
            </DialogContent>
        </Dialog>
    )
}
