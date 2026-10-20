import { Input } from "~/components/ui/Input"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "~/components/ui/Select"
import { useLocalize } from "~/hooks"
import type { ManagedAgentTaxonomyItem } from "~/api/managedAgents"

export interface ManagedAgentFilterState {
    keyword: string
    scenarioCode: string
    industryCode: string
}

interface Props {
    value: ManagedAgentFilterState
    taxonomies: ManagedAgentTaxonomyItem[]
    onChange: (next: ManagedAgentFilterState) => void
}

export function ManagedAgentFilters({ value, taxonomies, onChange }: Props) {
    const localize = useLocalize()
    const scenarios = taxonomies.filter((one) => one.kind === "scenario")
    const industries = taxonomies.filter((one) => one.kind === "industry")
    return (
        <div className="flex flex-wrap items-center gap-2">
            <Input
                className="max-w-[260px]"
                value={value.keyword}
                placeholder={localize("com_managed_agents_search")}
                onChange={(e) => onChange({ ...value, keyword: e.target.value })}
            />
            <Select
                value={value.scenarioCode || "__all"}
                onValueChange={(v) => onChange({ ...value, scenarioCode: v === "__all" ? "" : v })}
            >
                <SelectTrigger className="w-[160px]"><SelectValue placeholder={localize("com_managed_agents_scenario")} /></SelectTrigger>
                <SelectContent>
                    <SelectItem value="__all">{localize("com_managed_agents_all_scenarios")}</SelectItem>
                    {scenarios.map((one) => <SelectItem key={one.code} value={one.code}>{one.name_zh}</SelectItem>)}
                </SelectContent>
            </Select>
            <Select
                value={value.industryCode || "__all"}
                onValueChange={(v) => onChange({ ...value, industryCode: v === "__all" ? "" : v })}
            >
                <SelectTrigger className="w-[160px]"><SelectValue placeholder={localize("com_managed_agents_industry")} /></SelectTrigger>
                <SelectContent>
                    <SelectItem value="__all">{localize("com_managed_agents_all_industries")}</SelectItem>
                    {industries.map((one) => <SelectItem key={one.code} value={one.code}>{one.name_zh}</SelectItem>)}
                </SelectContent>
            </Select>
        </div>
    )
}
