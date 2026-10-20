import { useQuery } from "@tanstack/react-query"
import { useState } from "react"
import {
    getManagedAgentCatalogApi,
    getManagedAgentTaxonomiesApi,
    type ManagedAgentCatalogItem,
} from "~/api/managedAgents"
import { Dialog, DialogContent, DialogHeader, DialogTitle } from "~/components/ui/Dialog"
import { LoadingIcon } from "~/components/ui/icon/Loading"
import { useLocalize } from "~/hooks"
import { ManagedAgentCard } from "./ManagedAgentCard"
import { ManagedAgentFilters, type ManagedAgentFilterState } from "./ManagedAgentFilters"

const EMPTY_FILTERS: ManagedAgentFilterState = { keyword: "", scenarioCode: "", industryCode: "" }

export function ManagedAgentTab() {
    const localize = useLocalize()
    const [filters, setFilters] = useState<ManagedAgentFilterState>(EMPTY_FILTERS)
    const [preview, setPreview] = useState<ManagedAgentCatalogItem | null>(null)

    const taxonomiesQuery = useQuery({
        queryKey: ["managed-agent-taxonomies"],
        queryFn: () => getManagedAgentTaxonomiesApi(),
        staleTime: 5 * 60_000,
    })
    const catalogQuery = useQuery({
        queryKey: ["managed-agent-catalog", filters],
        queryFn: () =>
            getManagedAgentCatalogApi({
                keyword: filters.keyword.trim() || undefined,
                scenario_code: filters.scenarioCode || undefined,
                industry_code: filters.industryCode || undefined,
                page_size: 50,
            }),
        staleTime: 30_000,
    })

    const taxonomies = taxonomiesQuery.data?.data ?? []
    const agents = catalogQuery.data?.data?.data ?? []

    return (
        <div className="flex w-full max-w-[1000px] flex-1 flex-col gap-4 px-5 pb-5">
            <ManagedAgentFilters value={filters} taxonomies={taxonomies} onChange={setFilters} />
            {catalogQuery.isLoading ? (
                <div className="flex flex-1 items-center justify-center py-20 text-blue-500">
                    <LoadingIcon className="size-10" />
                </div>
            ) : agents.length === 0 ? (
                <div className="py-20 text-center text-body-sm text-text-3">{localize("com_managed_agents_empty")}</div>
            ) : (
                <div className="grid w-full items-start gap-4 sm:grid-cols-2 lg:grid-cols-3">
                    {agents.map((agent) => (
                        <ManagedAgentCard key={agent.id} agent={agent} onOpen={setPreview} />
                    ))}
                </div>
            )}

            <Dialog open={Boolean(preview)} onOpenChange={(open) => !open && setPreview(null)}>
                <DialogContent className="max-w-lg">
                    <DialogHeader><DialogTitle>{preview?.name}</DialogTitle></DialogHeader>
                    {preview && (
                        <div className="space-y-2 text-body-sm text-text-2">
                            {preview.description && <p>{preview.description}</p>}
                            <div>{localize("com_managed_agents_source")}: {preview.source_kind}</div>
                            <div>{localize("com_managed_agents_protocol")}: {preview.protocol}</div>
                            <div>{localize("com_managed_agents_scenario")}: {preview.scenario_name || "—"}</div>
                            <div>{localize("com_managed_agents_industry")}: {preview.industry_name || "—"}</div>
                            <div>{localize("com_managed_agents_health")}: {preview.health_status}</div>
                            <p className="pt-2 text-caption text-text-3">{localize("com_managed_agents_preview_only")}</p>
                        </div>
                    )}
                </DialogContent>
            </Dialog>
        </div>
    )
}
