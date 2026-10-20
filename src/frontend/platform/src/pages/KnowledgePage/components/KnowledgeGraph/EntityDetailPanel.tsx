// @ts-strict-ignore
import { LoadingIcon } from "@/components/bs-icons/loading"
import { Button } from "@/components/bs-ui/button"
import type { KnowledgeGraphEntityDetail } from "@/controllers/API/knowledgeGraph"
import { ArrowLeft, ArrowRight, Maximize2, X } from "lucide-react"
import { useTranslation } from "react-i18next"

interface EntityDetailPanelProps {
    detail: KnowledgeGraphEntityDetail | null
    loading: boolean
    selectedEntityId: number | null
    onSelectEntity: (entityId: number) => void
    onExplore: (entityId: number) => void
    onClose: () => void
}

export function EntityDetailPanel({
    detail,
    loading,
    selectedEntityId,
    onSelectEntity,
    onExplore,
    onClose,
}: EntityDetailPanelProps) {
    const { t } = useTranslation("knowledge")

    if (!selectedEntityId) {
        return (
            <div className="flex h-full items-center justify-center px-6 text-center text-[12px] leading-5 text-[#86909c]">
                {t("knowledgeGraph.selectNodeHint")}
            </div>
        )
    }

    if (loading) {
        return (
            <div className="flex h-full items-center justify-center">
                <LoadingIcon />
            </div>
        )
    }

    if (!detail) {
        return (
            <div className="flex h-full items-center justify-center px-6 text-center text-[12px] text-[#86909c]">
                {t("knowledgeGraph.entityLoadFailed")}
            </div>
        )
    }

    const { entity, relations, evidence } = detail
    const attributes = entity.attributes && typeof entity.attributes === "object"
        ? Object.entries(entity.attributes)
        : []

    return (
        <div className="flex h-full flex-col">
            <div className="flex items-start justify-between gap-2 border-b border-[#EBECF0] px-4 py-3">
                <div className="min-w-0">
                    <div className="truncate text-[15px] font-medium text-[#212121]">{entity.name}</div>
                    <div className="mt-1 flex items-center gap-2 text-[12px] text-[#86909c]">
                        <span className="rounded bg-[#F2F3F5] px-1.5 py-0.5">{entity.type}</span>
                        <span>{t("knowledgeGraph.mentionCount", { count: entity.mention_count })}</span>
                    </div>
                </div>
                <div className="flex shrink-0 items-center gap-1">
                    <Button
                        variant="ghost"
                        className="h-7 w-7 p-0"
                        title={t("knowledgeGraph.viewNeighbors")}
                        onClick={() => onExplore(entity.id)}
                    >
                        <Maximize2 size={14} />
                    </Button>
                    <Button variant="ghost" className="h-7 w-7 p-0" onClick={onClose}>
                        <X size={14} />
                    </Button>
                </div>
            </div>

            <div className="min-h-0 flex-1 space-y-4 overflow-y-auto px-4 py-3 text-[12px] leading-5 text-[#4E5969]">
                {entity.description && <p className="whitespace-pre-wrap">{entity.description}</p>}

                <section>
                    <div className="mb-1 text-[12px] font-medium text-[#212121]">{t("knowledgeGraph.attributes")}</div>
                    {attributes.length ? (
                        <div className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1">
                            {attributes.map(([key, value]) => (
                                <div key={key} className="contents">
                                    <span className="text-[#86909c]">{key}</span>
                                    <span className="break-all">{String(value)}</span>
                                </div>
                            ))}
                        </div>
                    ) : (
                        <span className="text-[#86909c]">{t("knowledgeGraph.noAttributes")}</span>
                    )}
                </section>

                <section>
                    <div className="mb-1 text-[12px] font-medium text-[#212121]">{t("knowledgeGraph.relations")}</div>
                    {relations.length ? (
                        <div className="space-y-1.5">
                            {relations.map((relation) => (
                                <button
                                    key={`${relation.id}-${relation.other_entity.id}`}
                                    type="button"
                                    className="flex w-full items-center gap-1.5 rounded border border-[#EBECF0] px-2 py-1.5 text-left hover:bg-[#F7F8FA]"
                                    onClick={() => onSelectEntity(relation.other_entity.id)}
                                >
                                    {relation.direction === "out" ? <ArrowRight size={12} /> : <ArrowLeft size={12} />}
                                    <span className="shrink-0 rounded bg-[#E8F3FF] px-1.5 py-0.5 text-[#4D9BF0]">
                                        {relation.label}
                                    </span>
                                    <span className="truncate text-[#212121]">{relation.other_entity.name}</span>
                                </button>
                            ))}
                        </div>
                    ) : (
                        <span className="text-[#86909c]">{t("knowledgeGraph.noRelations")}</span>
                    )}
                </section>

                <section>
                    <div className="mb-1 text-[12px] font-medium text-[#212121]">{t("knowledgeGraph.evidence")}</div>
                    {evidence.length ? (
                        <div className="space-y-2">
                            {evidence.map((item) => (
                                <div key={`${item.document_id}-${item.chunk_index}`} className="rounded border border-[#EBECF0] px-2 py-1.5">
                                    <div className="mb-1 flex items-center justify-between gap-2 text-[11px] text-[#86909c]">
                                        <span className="truncate">{item.document_name || `#${item.document_id}`}</span>
                                        <span className="shrink-0">chunk {item.chunk_index}</span>
                                    </div>
                                    <p className="line-clamp-4 whitespace-pre-wrap text-[#4E5969]">
                                        {item.snippet || t("knowledgeGraph.noSnippet")}
                                    </p>
                                </div>
                            ))}
                        </div>
                    ) : (
                        <span className="text-[#86909c]">{t("knowledgeGraph.noEvidence")}</span>
                    )}
                </section>
            </div>
        </div>
    )
}
