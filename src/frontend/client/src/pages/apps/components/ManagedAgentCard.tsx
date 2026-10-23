import { Badge } from "~/components/ui/Badge"
import { useLocalize } from "~/hooks"
import type { ManagedAgentCatalogItem } from "~/api/managedAgents"

interface Props {
    agent: ManagedAgentCatalogItem
    onOpen: (agent: ManagedAgentCatalogItem) => void
}

const SOURCE_KEY: Record<ManagedAgentCatalogItem["source_kind"], string> = {
    native: "com_managed_agents_source_native",
    low_code: "com_managed_agents_source_low_code",
    high_code: "com_managed_agents_source_high_code",
}

export function ManagedAgentCard({ agent, onOpen }: Props) {
    const localize = useLocalize()
    return (
        <button
            type="button"
            onClick={() => onOpen(agent)}
            className="flex h-full flex-col rounded-xl border border-border-base bg-white p-4 text-left transition-colors hover:border-blue-300 fine-pointer:hover:bg-fill-1/60"
        >
            <div className="mb-2 flex items-start justify-between gap-2">
                <div className="min-w-0">
                    <div className="truncate font-medium text-text-1">{agent.name}</div>
                    <div className="mt-1 text-caption text-text-3">
                        {localize(SOURCE_KEY[agent.source_kind])} · {localize(`com_managed_agents_protocol_${agent.protocol}`)}
                    </div>
                </div>
                <Badge variant={agent.health_status === "healthy" ? "default" : "secondary"}>
                    {localize(`com_managed_agents_health_${agent.health_status}`)}
                </Badge>
            </div>
            {agent.description && <p className="line-clamp-2 text-body-sm text-text-2">{agent.description}</p>}
            <div className="mt-3 flex flex-wrap gap-1.5 text-caption text-text-3">
                {agent.scenario_name && <span className="rounded bg-fill-1 px-2 py-0.5">{agent.scenario_name}</span>}
                {agent.industry_name && <span className="rounded bg-fill-1 px-2 py-0.5">{agent.industry_name}</span>}
                {(agent.tag_names || []).map((name) => (
                    <span key={name} className="rounded bg-blue-500/[0.07] px-2 py-0.5 text-blue-500">{name}</span>
                ))}
            </div>
        </button>
    )
}
