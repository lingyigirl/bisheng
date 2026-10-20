// @ts-strict-ignore
import { Button } from "@/components/bs-ui/button"
import { Network } from "lucide-react"
import { useState } from "react"
import { useTranslation } from "react-i18next"
import { KnowledgeGraphDialog } from "./KnowledgeGraphDialog"

interface KnowledgeGraphButtonProps {
    knowledgeId: string | number
    knowledgeType?: number | null
    canEditKb?: boolean
}

/**
 * Entry point for the manual knowledge-graph build. Only normal document
 * knowledge bases (type=0) expose it; QA/private/space libraries have a
 * different chunk model and are out of scope for v1.
 */
export function KnowledgeGraphButton({
    knowledgeId,
    knowledgeType,
    canEditKb = false,
}: KnowledgeGraphButtonProps) {
    const { t } = useTranslation("knowledge")
    const [open, setOpen] = useState(false)

    if (Number(knowledgeType) !== 0) return null

    return (
        <>
            <Button
                variant="outline"
                className="px-2 md:px-4 whitespace-nowrap h-9"
                onClick={() => setOpen(true)}
            >
                <Network size={16} strokeWidth={1.5} className="mr-0 md:mr-1" />
                <span className="hidden md:inline">{t("knowledgeGraph.title")}</span>
            </Button>
            <KnowledgeGraphDialog
                knowledgeId={Number(knowledgeId)}
                canEditKb={canEditKb}
                open={open}
                onOpenChange={setOpen}
            />
        </>
    )
}
