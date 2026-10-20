import { Input } from '~/components/ui/Input';
import { Textarea } from '~/components/ui/Textarea';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '~/components/ui/Select';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '~/components/ui/Tabs';
import { useLocalize } from '~/hooks';
import type { HarnessOptions, SubagentConfig } from '~/types/harness';
import { SubagentResources } from './SubagentResources';

interface SubagentEditorProps {
    member: SubagentConfig;
    options: HarnessOptions;
    disabled?: boolean;
    onChange: (member: SubagentConfig) => void;
}

export function SubagentEditor({ member, options, disabled, onChange }: SubagentEditorProps) {
    const t = useLocalize();
    const models = [...new Map((options.models || []).filter((model) => /^\d+$/.test(String(model.id))).map((model) => [String(model.id), model])).values()];
    const patch = (value: Partial<SubagentConfig>) => onChange({ ...member, ...value });
    const resourceCount = member.skill_names.length + member.tool_refs.length + member.connector_refs.length;
    return (
        <Tabs defaultValue="basics" className="text-body-sm text-text-1">
            <TabsList className="mb-4 grid w-full grid-cols-2 gap-1 bg-fill-2 p-1" aria-label={t('harness.memberSettings')}>
                <TabsTrigger value="basics" className="min-w-0 text-body-sm text-text-2 data-[state=active]:text-text-1">{t('harness.basicSettings')}</TabsTrigger>
                <TabsTrigger value="resources" className="min-w-0 gap-2 text-body-sm text-text-2 data-[state=active]:text-text-1">
                    {t('harness.resourceSettings')}
                    <span className="rounded bg-fill-3 px-1.5 text-caption tabular-nums">{resourceCount}</span>
                </TabsTrigger>
            </TabsList>
            <TabsContent value="basics" className="m-0 space-y-5 p-0">
                <label className="block space-y-2">
                    <span>{t('harness.name')}</span>
                    <Input disabled={disabled} value={member.name} maxLength={64} onChange={(e) => patch({ name: e.target.value })} />
                </label>
                <label className="block space-y-2">
                    <span>{t('harness.description')}</span>
                    <Textarea disabled={disabled} value={member.description} maxLength={1000} onChange={(e) => patch({ description: e.target.value })} />
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
                <details>
                    <summary className="cursor-pointer text-text-2">{t('harness.prompt')}</summary>
                    <Textarea disabled={disabled} className="mt-3 min-h-32" aria-label={t('harness.prompt')} value={member.system_prompt} maxLength={12000} onChange={(e) => patch({ system_prompt: e.target.value })} />
                </details>
            </TabsContent>
            <TabsContent value="resources" className="m-0 p-0">
                <SubagentResources member={member} options={options} disabled={disabled} onChange={onChange} />
            </TabsContent>
        </Tabs>
    );
}
