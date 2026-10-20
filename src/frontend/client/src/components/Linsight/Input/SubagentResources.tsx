import { Button } from '@bisheng/ui';
import { Outlined } from 'bisheng-icons';
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
type ResourceGroup = {
    id: string;
    name: string;
    kind: 'builtin' | 'api' | 'mcp';
    actions: Resource[];
    unavailable?: boolean;
};
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
    const [expandedGroups, setExpandedGroups] = useState<Set<string>>(new Set());
    const listRef = useRef<HTMLDivElement>(null);
    const skills = useQuery({ queryKey: ['linsight', 'selectableSkills'], queryFn: getSelectableSkills, staleTime: 60_000 });
    const catalog = useQuery({ queryKey: ['linsight', 'memberToolCatalog'], queryFn: getMemberToolCatalog, staleTime: 60_000 });
    const catalogGroups = useMemo(
        () => catalog.data || options.tools || [],
        [catalog.data, options.tools],
    );
    const resources = useMemo(() => {
        const result: Record<ResourceField, Resource[]> = {
            skill_names: (skills.data || []).map((skill) => ({ id: skill.name, name: skill.display_name || skill.name, detail: skill.description, searchText: `${skill.display_name} ${skill.name} ${skill.description}`.toLowerCase() })),
            tool_refs: [], connector_refs: [],
        };
        for (const group of catalogGroups) {
            const field = group.is_preset === 1 ? 'tool_refs' : 'connector_refs';
            result[field].push(...(group.children || []).map((tool) => ({ id: String(tool.id), name: tool.name, detail: group.name, searchText: `${tool.name} ${group.name}`.toLowerCase() })));
        }
        return result;
    }, [skills.data, catalogGroups]);
    const selectedIds = useMemo(() => new Set(member[category].map(String)), [member, category]);
    const rows = useMemo(() => {
        const available = resources[category];
        const known = new Set(available.map((item) => item.id));
        const missing = [...selectedIds].filter((id) => !known.has(id)).map((id) => ({ id, name: id, detail: '', searchText: id.toLowerCase(), unavailable: true }));
        return [...available, ...missing];
    }, [resources, category, selectedIds]);
    const resourceGroups = useMemo<ResourceGroup[]>(() => {
        const available = catalogGroups
            .filter((group) => (category === 'tool_refs' ? group.is_preset === 1 : group.is_preset !== 1))
            .map((group) => ({
                id: String(group.id),
                name: group.name,
                kind: group.is_preset === 1 ? 'builtin' as const : group.is_preset === 2 ? 'mcp' as const : 'api' as const,
                actions: (group.children || []).map((tool) => ({
                    id: String(tool.id),
                    name: tool.name,
                    detail: group.name,
                    searchText: `${tool.name} ${group.name}`.toLowerCase(),
                })),
            }));
        const known = new Set(available.flatMap((group) => group.actions.map((action) => action.id)));
        const missing = [...selectedIds]
            .filter((id) => !known.has(id))
            .map((id) => ({
                id,
                name: id,
                detail: '',
                searchText: id.toLowerCase(),
                unavailable: true,
            }));
        return missing.length
            ? [...available, { id: '__unavailable__', name: '', kind: 'api', actions: missing, unavailable: true }]
            : available;
    }, [catalogGroups, category, selectedIds]);
    const query = search.trim().toLowerCase();
    const filtered = useMemo(() => rows.filter((item) => (!selectedOnly || selectedIds.has(item.id)) && item.searchText.includes(query)), [rows, query, selectedOnly, selectedIds]);
    const visibleResourceGroups = useMemo(() => {
        const unavailableLabel = category === 'connector_refs'
            ? t('harness.unavailableConnector')
            : t('harness.unavailableTools');
        return resourceGroups
            .map((group) => {
                const groupName = group.unavailable ? unavailableLabel : group.name;
                const groupMatches = groupName.toLowerCase().includes(query);
                const actions = group.actions
                    .filter((action) => !selectedOnly || selectedIds.has(action.id))
                    .filter((action) => !query || groupMatches || action.searchText.includes(query));
                return { ...group, groupName, actions };
            })
            .filter((group) => group.actions.length > 0);
    }, [resourceGroups, category, query, selectedIds, selectedOnly, t]);
    const isGrouped = category !== 'skill_names';
    const pageCount = isGrouped ? 1 : Math.max(1, Math.ceil(filtered.length / PAGE_SIZE));
    const currentPage = isGrouped ? 0 : Math.min(page, pageCount - 1);
    useEffect(() => {
        // Reset scrolling without remounting every checkbox on each keystroke.
        if (listRef.current) listRef.current.scrollTop = 0;
    }, [category, currentPage, search, selectedOnly]);
    useEffect(() => {
        setExpandedGroups(new Set());
    }, [category]);
    const activeQuery = category === 'skill_names' ? skills : catalog;
    const kind = t(categories.find((item) => item.field === category)!.label);
    const handleToggle = (id: string) => {
        if (disabled) return;
        const next = new Set(selectedIds);
        if (next.has(id)) next.delete(id);
        else next.add(id);
        onChange({ ...member, [category]: category === 'skill_names' ? [...next] : [...next].map(Number) });
    };
    const toggleGroup = (id: string) => {
        setExpandedGroups((current) => {
            const next = new Set(current);
            if (next.has(id)) next.delete(id);
            else next.add(id);
            return next;
        });
    };
    const renderResource = (item: Resource) => (
        <label key={item.id} className="flex cursor-pointer items-start gap-2 rounded-md px-2 py-2 hover:bg-fill-2">
            <Checkbox disabled={disabled} checked={selectedIds.has(item.id)} className="mt-0.5 shrink-0" onCheckedChange={() => handleToggle(item.id)} />
            <span className="min-w-0 break-words">
                <span className={item.unavailable ? 'text-text-3' : 'text-text-1'}>
                    {item.unavailable ? (category === 'skill_names' ? t('harness.unavailableSkill', { name: item.name }) : t('harness.unavailableCapability', { kind, id: item.id })) : item.name}
                </span>
                {category !== 'skill_names' && item.detail && <span className="block text-caption text-text-3">{item.detail}</span>}
            </span>
        </label>
    );

    return (
        <Tabs value={category} onValueChange={(value) => { setCategory(value as ResourceField); setPage(0); }} className="flex h-full min-h-0 flex-col rounded-lg border border-border-base">
            <TabsList className="grid w-full grid-cols-3 gap-1 rounded-b-none bg-fill-2 p-1" aria-label={t('harness.resourceCategories')}>
                {categories.map(({ field, label }) => (
                    <TabsTrigger key={field} value={field} className="min-w-0 gap-1.5 px-2 text-body-sm text-text-2 data-[state=active]:text-text-1">
                        {t(label)}
                        <span className="rounded bg-fill-3 px-1.5 text-caption tabular-nums">{member[field].length}</span>
                    </TabsTrigger>
                ))}
            </TabsList>
            <TabsContent key={category} value={category} className="m-0 flex min-h-0 flex-1 flex-col space-y-3 p-3">
                <Input type="search" value={search} aria-label={t('harness.searchResources')} placeholder={t('harness.searchResources')} onChange={(event) => { setSearch(event.target.value); setPage(0); }} />
                <div className="flex items-center justify-between gap-2 text-caption text-text-3">
                    <span role="status">{t('harness.resourceCount', { selected: selectedIds.size, total: rows.length })}</span>
                    <label className="flex cursor-pointer items-center gap-2 text-text-2">
                        <Checkbox checked={selectedOnly} onCheckedChange={(checked) => { setSelectedOnly(checked === true); setPage(0); }} />
                        {t('harness.selectedOnly')}
                    </label>
                </div>
                <div ref={listRef} role="group" aria-label={kind} tabIndex={0} className="min-h-56 flex-1 space-y-1 overflow-y-auto overscroll-contain rounded-md outline-none focus-visible:ring-2 focus-visible:ring-blue-500">
                    {activeQuery.isLoading && <p className="p-2 text-text-3">{t('harness.loading')}</p>}
                    {activeQuery.isError && <p role="alert" className="p-2 text-danger">{t('harness.loadError')}</p>}
                    {activeQuery.isSuccess && (isGrouped ? visibleResourceGroups.length === 0 : filtered.length === 0) && <p className="p-2 text-text-3">{t('harness.noMatchingResources')}</p>}
                    {isGrouped
                        ? visibleResourceGroups.map((group) => {
                            const selectedCount = group.actions.filter((action) => selectedIds.has(action.id)).length;
                            const expanded = Boolean(query) || selectedOnly || group.unavailable || expandedGroups.has(group.id);
                            const groupKind = group.kind === 'builtin' ? t('harness.builtinTool') : group.kind.toUpperCase();
                            return (
                                <div key={group.id} className="overflow-hidden rounded-md border border-border-base">
                                    <button
                                        type="button"
                                        aria-expanded={expanded}
                                        className="flex w-full items-center gap-2 bg-fill-1 px-3 py-2 text-left hover:bg-fill-2"
                                        onClick={() => toggleGroup(group.id)}
                                    >
                                        <Outlined.Down size={14} className={expanded ? 'shrink-0' : 'shrink-0 -rotate-90'} />
                                        <span className="min-w-0 flex-1 truncate text-text-1">
                                            {group.groupName || t('harness.unavailableTools')}
                                        </span>
                                        {!group.unavailable && <span className="rounded bg-fill-3 px-1.5 text-caption text-text-3">{groupKind}</span>}
                                        <span className="shrink-0 text-caption text-text-3">
                                            {t('harness.resourceCount', { selected: selectedCount, total: group.actions.length })}
                                        </span>
                                    </button>
                                    {expanded && <div className="space-y-1 border-t border-border-base p-1">{group.actions.map(renderResource)}</div>}
                                </div>
                            );
                        })
                        : filtered.slice(currentPage * PAGE_SIZE, (currentPage + 1) * PAGE_SIZE).map(renderResource)}
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
