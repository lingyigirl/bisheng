import { useQuery } from '@tanstack/react-query';
import { getSelectableSkills } from '~/api/linsight';
import { getMemberToolCatalog } from '~/api/harness';
import { Checkbox } from '~/components/ui/Checkbox';
import { Input } from '~/components/ui/Input';
import { Textarea } from '~/components/ui/Textarea';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '~/components/ui/Select';
import { useLocalize } from '~/hooks';
import type { HarnessOptions, SubagentConfig } from '~/types/harness';

interface SubagentEditorProps {
    member: SubagentConfig;
    options: HarnessOptions;
    disabled?: boolean;
    onChange: (member: SubagentConfig) => void;
}

function toggle<T>(items: T[], value: T): T[] {
    return items.includes(value) ? items.filter((item) => item !== value) : [...items, value];
}

export function SubagentEditor({ member, options, disabled, onChange }: SubagentEditorProps) {
    const t = useLocalize();
    const skills = useQuery({ queryKey: ['linsight', 'selectableSkills'], queryFn: getSelectableSkills });
    const catalog = useQuery({ queryKey: ['linsight', 'memberToolCatalog'], queryFn: getMemberToolCatalog, staleTime: 60_000 });
    const tools = catalog.data || options.tools || [];
    const missingCapabilities = (['tool_refs', 'connector_refs'] as const).flatMap((field) =>
        member[field].filter((id) => !tools.some((group) =>
            (group.is_preset === 1 ? 'tool_refs' : 'connector_refs') === field && group.children?.some((tool) => Number(tool.id) === id),
        )).map((id) => ({ field, id })),
    );
    const models = [...new Map((options.models || []).filter((model) => /^\d+$/.test(String(model.id))).map((model) => [String(model.id), model])).values()];
    const patch = (value: Partial<SubagentConfig>) => onChange({ ...member, ...value });
    return (
        <fieldset disabled={disabled} className="space-y-5 text-body-sm text-text-1">
            <label className="block space-y-2">
                <span>{t('harness.name')}</span>
                <Input value={member.name} maxLength={64} onChange={(e) => patch({ name: e.target.value })} />
            </label>
            <label className="block space-y-2">
                <span>{t('harness.description')}</span>
                <Textarea value={member.description} maxLength={1000} onChange={(e) => patch({ description: e.target.value })} />
            </label>
            <div className="space-y-2">
                <span>{t('harness.model')}</span>
                <Select disabled={disabled} value={member.model_id || 'inherit'} onValueChange={(value) => patch({ model_id: value === 'inherit' ? null : value })}>
                    <SelectTrigger aria-label={t('harness.model')}><SelectValue /></SelectTrigger>
                    <SelectContent>
                        <SelectItem value="inherit">{t('harness.inherit')}</SelectItem>
                        {models.map((model) => (
                            <SelectItem key={model.id} value={String(model.id)}>{model.displayName || model.name || model.id}</SelectItem>
                        ))}
                    </SelectContent>
                </Select>
            </div>
            <div className="space-y-2">
                <p>{t('harness.skills')}</p>
                {skills.isLoading && <p className="text-text-3">{t('harness.loading')}</p>}
                {skills.isError && <p className="text-danger">{t('harness.loadError')}</p>}
                {skills.data?.map((skill) => (
                    <label key={skill.name} className="flex items-center gap-2 py-1">
                        <Checkbox disabled={disabled} checked={member.skill_names.includes(skill.name)} onCheckedChange={() => patch({ skill_names: toggle(member.skill_names, skill.name) })} />
                        {skill.display_name || skill.name}
                    </label>
                ))}
            </div>
            <div className="space-y-3">
                <p>{t('harness.capabilities')}</p>
                {catalog.isLoading && <p className="text-text-3">{t('harness.loading')}</p>}
                {catalog.isError && <p className="text-danger">{t('harness.loadError')}</p>}
                {tools.map((group) => {
                    const field = group.is_preset === 1 ? 'tool_refs' : 'connector_refs';
                    return (
                        <div key={group.id} className="rounded-lg border border-border-base p-3">
                            <p className="mb-2 text-caption text-text-3">{group.name} · {t(field === 'tool_refs' ? 'harness.tools' : 'harness.connectors')}</p>
                            {(group.children || []).map((tool) => (
                                <label key={tool.id} className="flex items-center gap-2 py-1">
                                    <Checkbox disabled={disabled} checked={member[field].includes(Number(tool.id))} onCheckedChange={() => patch({ [field]: toggle(member[field], Number(tool.id)) })} />
                                    {tool.name}
                                </label>
                            ))}
                        </div>
                    );
                })}
                {missingCapabilities.map(({ field, id }) => (
                    <label key={`${field}-${id}`} className="flex items-center gap-2 py-1">
                        <Checkbox disabled={disabled} checked onCheckedChange={() => patch({ [field]: toggle(member[field], id) })} />
                        {t('harness.unavailableCapability', { kind: t(field === 'tool_refs' ? 'harness.tools' : 'harness.connectors'), id })}
                    </label>
                ))}
            </div>
            <details>
                <summary className="cursor-pointer text-text-2">{t('harness.prompt')}</summary>
                <Textarea className="mt-3 min-h-32" aria-label={t('harness.prompt')} value={member.system_prompt} maxLength={12000} onChange={(e) => patch({ system_prompt: e.target.value })} />
            </details>
        </fieldset>
    );
}
