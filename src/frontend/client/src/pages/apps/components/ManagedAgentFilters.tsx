import { Input } from "~/components/ui/Input"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "~/components/ui/Select"
import { useLocalize } from "~/hooks"
import { useTranslation } from "react-i18next"
import type { ManagedAgentTaxonomyItem } from "~/api/managedAgents"

export interface ManagedAgentFilterState {
    keyword: string
    scenarioCode: string
    industryCode: string
    tag: string
}

interface Props {
    value: ManagedAgentFilterState
    taxonomies: ManagedAgentTaxonomyItem[]
    tags: string[]
    onChange: (next: ManagedAgentFilterState) => void
}

export function ManagedAgentFilters({ value, taxonomies, tags, onChange }: Props) {
    const localize = useLocalize()
    const { i18n } = useTranslation()
    const nameFor = (one: ManagedAgentTaxonomyItem) => {
        if (i18n.language.startsWith("en")) return one.name_en || one.name_zh
        if (i18n.language.startsWith("ja")) return one.name_ja || one.name_zh
        return one.name_zh
    }
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
                    {scenarios.map((one) => <SelectItem key={one.code} value={one.code}>{nameFor(one)}</SelectItem>)}
                </SelectContent>
            </Select>
            <Select
                value={value.industryCode || "__all"}
                onValueChange={(v) => onChange({ ...value, industryCode: v === "__all" ? "" : v })}
            >
                <SelectTrigger className="w-[160px]"><SelectValue placeholder={localize("com_managed_agents_industry")} /></SelectTrigger>
                <SelectContent>
                    <SelectItem value="__all">{localize("com_managed_agents_all_industries")}</SelectItem>
                    {industries.map((one) => <SelectItem key={one.code} value={one.code}>{nameFor(one)}</SelectItem>)}
                </SelectContent>
            </Select>
            {tags.length > 0 && (
                <Select
                    value={value.tag || "__all"}
                    onValueChange={(v) => onChange({ ...value, tag: v === "__all" ? "" : v })}
                >
                    <SelectTrigger className="w-[160px]"><SelectValue placeholder={localize("com_managed_agents_tags")} /></SelectTrigger>
                    <SelectContent>
                        <SelectItem value="__all">{localize("com_managed_agents_all_tags")}</SelectItem>
                        {tags.map((tag) => <SelectItem key={tag} value={tag}>{tag}</SelectItem>)}
                    </SelectContent>
                </Select>
            )}
        </div>
    )
}
