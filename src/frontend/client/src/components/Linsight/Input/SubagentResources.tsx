import { Button } from '@bisheng/ui';
import { useQuery } from '@tanstack/react-query';
import { useEffect, useMemo, useRef, useState } from 'react';
import { getMemberToolCatalog } from '~/api/harness';
import { getSelectableSkills } from '~/api/linsight';
import { Checkbox } from '~/components/ui/Checkbox';
import { Input } from '~/components/ui/Input';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '~/components/ui/Tabs';
import { useLocalize } from '~/hooks';
import type { HarnessOptions, SubagentConfig } from '~/types/harness';

interface SubagentResourcesProps {
    member: SubagentConfig;
    options: HarnessOptions;
    disabled?: boolean;
    onChange: (member: SubagentConfig) => void;
}

type ResourceField = 'skill_names' | 'tool_refs' | 'connector_refs';
type Resource = { id: string; name: string; detail: string; searchText: string; unavailable?: boolean };
const categories = [
    { field: 'skill_names', label: 'harness.skills' },
    { field: 'tool_refs', label: 'harness.tools' },
    { field: 'connector_refs', label: 'harness.connectors' },
] as const;
const PAGE_SIZE = 40;

export function SubagentResources({ member, options, disabled, onChange }: SubagentResourcesProps) {
    const t = useLocalize();
    const [category, setCategory] = useState<ResourceField>('skill_names');
    const [search, setSearch] = useState('');
    const [selectedOnly, setSelectedOnly] = useState(false);
    const [page, setPage] = useState(0);
    const listRef = useRef<HTMLDivElement>(null);
    const skills = useQuery({ queryKey: ['linsight', 'selectableSkills'], queryFn: getSelectableSkills, staleTime: 60_000 });
    const catalog = useQuery({ queryKey: ['linsight', 'memberToolCatalog'], queryFn: getMemberToolCatalog, staleTime: 60_000 });
    const resources = useMemo(() => {
        const result: Record<ResourceField, Resource[]> = {
            skill_names: (skills.data || []).map((skill) => ({ id: skill.name, name: skill.display_name || skill.name, detail: skill.description, searchText: `${skill.display_name} ${skill.name} ${skill.description}`.toLowerCase() })),
            tool_refs: [], connector_refs: [],
        };
        for (const group of catalog.data || options.tools || []) {
            const field = group.is_preset === 1 ? 'tool_refs' : 'connector_refs';
            result[field].push(...(group.children || []).map((tool) => ({ id: String(tool.id), name: tool.name, detail: group.name, searchText: `${tool.name} ${group.name}`.toLowerCase() })));
        }
        return result;
    }, [skills.data, catalog.data, options.tools]);
    const selectedIds = useMemo(() => new Set(member[category].map(String)), [member, category]);
    const rows = useMemo(() => {
        const available = resources[category];
        const known = new Set(available.map((item) => item.id));
        const missing = [...selectedIds].filter((id) => !known.has(id)).map((id) => ({ id, name: id, detail: '', searchText: id.toLowerCase(), unavailable: true }));
        return [...available, ...missing];
    }, [resources, category, selectedIds]);
    const query = search.trim().toLowerCase();
    const filtered = useMemo(() => rows.filter((item) => (!selectedOnly || selectedIds.has(item.id)) && item.searchText.includes(query)), [rows, query, selectedOnly, selectedIds]);
    const pageCount = Math.max(1, Math.ceil(filtered.length / PAGE_SIZE));
    const currentPage = Math.min(page, pageCount - 1);
    useEffect(() => {
        // Reset scrolling without remounting every checkbox on each keystroke.
        if (listRef.current) listRef.current.scrollTop = 0;
    }, [category, currentPage, search, selectedOnly]);
    const activeQuery = category === 'skill_names' ? skills : catalog;
    const kind = t(categories.find((item) => item.field === category)!.label);
    const handleToggle = (id: string) => {
        if (disabled) return;
        const next = new Set(selectedIds);
        if (next.has(id)) next.delete(id);
        else next.add(id);
        onChange({ ...member, [category]: category === 'skill_names' ? [...next] : [...next].map(Number) });
    };

    return (
        <Tabs value={category} onValueChange={(value) => { setCategory(value as ResourceField); setPage(0); }} className="rounded-lg border border-border-base">
            <TabsList className="grid w-full grid-cols-3 gap-1 rounded-b-none bg-fill-2 p-1" aria-label={t('harness.resourceCategories')}>
                {categories.map(({ field, label }) => (
                    <TabsTrigger key={field} value={field} className="min-w-0 gap-1.5 px-2 text-body-sm text-text-2 data-[state=active]:text-text-1">
                        {t(label)}
                        <span className="rounded bg-fill-3 px-1.5 text-caption tabular-nums">{member[field].length}</span>
                    </TabsTrigger>
                ))}
            </TabsList>
            <TabsContent key={category} value={category} className="m-0 space-y-3 p-3">
                <Input type="search" value={search} aria-label={t('harness.searchResources')} placeholder={t('harness.searchResources')} onChange={(event) => { setSearch(event.target.value); setPage(0); }} />
                <div className="flex items-center justify-between gap-2 text-caption text-text-3">
                    <span role="status">{t('harness.resourceCount', { selected: selectedIds.size, total: rows.length })}</span>
                    <label className="flex cursor-pointer items-center gap-2 text-text-2">
                        <Checkbox checked={selectedOnly} onCheckedChange={(checked) => { setSelectedOnly(checked === true); setPage(0); }} />
                        {t('harness.selectedOnly')}
                    </label>
                </div>
                <div ref={listRef} role="group" aria-label={kind} tabIndex={0} className="h-56 space-y-1 overflow-y-auto overscroll-contain rounded-md outline-none focus-visible:ring-2 focus-visible:ring-blue-500">
                    {activeQuery.isLoading && <p className="p-2 text-text-3">{t('harness.loading')}</p>}
                    {activeQuery.isError && <p role="alert" className="p-2 text-danger">{t('harness.loadError')}</p>}
                    {activeQuery.isSuccess && filtered.length === 0 && <p className="p-2 text-text-3">{t('harness.noMatchingResources')}</p>}
                    {filtered.slice(currentPage * PAGE_SIZE, (currentPage + 1) * PAGE_SIZE).map((item) => (
                        <label key={item.id} className="flex cursor-pointer items-start gap-2 rounded-md px-2 py-2 hover:bg-fill-2">
                            <Checkbox disabled={disabled} checked={selectedIds.has(item.id)} className="mt-0.5 shrink-0" onCheckedChange={() => handleToggle(item.id)} />
                            <span className="min-w-0 break-words">
                                <span className={item.unavailable ? 'text-text-3' : 'text-text-1'}>
                                    {item.unavailable ? (category === 'skill_names' ? t('harness.unavailableSkill', { name: item.name }) : t('harness.unavailableCapability', { kind, id: item.id })) : item.name}
                                </span>
                                {category !== 'skill_names' && item.detail && <span className="block text-caption text-text-3">{item.detail}</span>}
                            </span>
                        </label>
                    ))}
                </div>
                {pageCount > 1 && (
                    <div className="flex items-center justify-between gap-2 border-t border-border-base pt-2">
                        <Button type="button" color="default" variant="text" size="small" disabled={currentPage === 0} onClick={() => setPage(currentPage - 1)}>{t('harness.previousPage')}</Button>
                        <span className="text-caption text-text-3">{t('harness.resourcePage', { page: currentPage + 1, total: pageCount })}</span>
                        <Button type="button" color="default" variant="text" size="small" disabled={currentPage + 1 === pageCount} onClick={() => setPage(currentPage + 1)}>{t('harness.nextPage')}</Button>
                    </div>
                )}
            </TabsContent>
        </Tabs>
    );
}
