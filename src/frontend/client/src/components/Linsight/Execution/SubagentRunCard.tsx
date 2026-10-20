import { Outlined } from 'bisheng-icons';
import { useMemo, useState } from 'react';
import { Button } from '~/components/ui/Button';
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from '~/components/ui/Collapsible';
import type { MergedStep, SubagentGroup } from './execTypes';
import { useLocalize } from '~/hooks';
import { useExecutionTurnLive } from './executionLive';
import { MemberSteps } from './MemberSteps';
import { firstLine } from './stepUtils';
import { useElapsedTicker } from './useElapsedTicker';

interface SubagentRunCardProps {
    group: SubagentGroup;
}

interface MemberStatusProps {
    step: MergedStep;
    running: boolean;
}

function MemberStatus({ step, running }: MemberStatusProps) {
    const t = useLocalize();
    const { elapsedMs } = useElapsedTicker(
        step.startedAt != null ? step.startedAt * 1000 : null,
        step.endedAt != null ? step.endedAt * 1000 : null, running,
    );
    const stopped = step.extraInfo.job_status === 'killed' || step.running;
    const failed = step.extraInfo.outcome === 'error';
    const status = running ? 'harness.running' : stopped ? 'harness.stopped' : failed ? 'harness.failed' : 'harness.completed';
    const elapsed = Math.round(elapsedMs / 1000);
    return <span className="text-caption text-text-3" role="status">{t(status)}{elapsed > 0 ? ` · ${elapsed}s` : ''}</span>;
}

export function SubagentRunCard({ group }: SubagentRunCardProps) {
    const t = useLocalize();
    const live = useExecutionTurnLive();
    const agent = group.agents[0];
    const step = agent?.step;
    const delegated = step?.stepType === 'subagent';
    const running = Boolean(step && live && (delegated ? step.running : !step.extraInfo.job_status));
    const [manualOpen, setManualOpen] = useState<boolean | null>(null);
    const outputOpen = manualOpen ?? running;
    const steps = useMemo(() => agent ? delegated ? agent.children : [agent.step, ...agent.children] : [], [agent, delegated]);
    const clock = useMemo(() => {
        if (!step || delegated) return step;
        return { ...step, endedAt: Math.max(step.endedAt ?? 0, ...steps.map(item => item.endedAt ?? 0)) };
    }, [step, steps, delegated]);
    if (!agent || !step) return null;
    const roleLabels: Record<string, string> = {
        'general-purpose': 'harness.researcher', 'data-analyst': 'harness.analyst',
        'chart-designer': 'harness.chartDesigner', 'quality-reviewer': 'harness.qualityReviewer',
    };
    const rawName = String(step.extraInfo.agent_name || '');
    const name = roleLabels[rawName] ? t(roleLabels[rawName]) : rawName || t('com_linsight_subagent_track', { 0: String(agent.idx ?? 1) });
    const goal = step.extraInfo.delegate_goal || group.goals?.[0] || (delegated ? step.callReason : '');
    return (
        <section data-agent-run={String(step.extraInfo.invocation_id || step.namespace || step.callId)} className="min-w-0 rounded-xl border border-border-base bg-fill-1 p-4">
            <div className="flex items-center justify-between gap-3">
                <span className="text-body font-medium text-text-1">{String(name)}{rawName && agent.idx ? ` · #${agent.idx}` : ''}</span>
                <MemberStatus step={clock || step} running={running} />
            </div>
            {goal && <details className="mt-2 text-body-sm text-text-2">
                <summary className="cursor-pointer">{firstLine(String(goal), 100)}</summary>
                <p className="mt-2 whitespace-pre-wrap">{String(goal)}</p>
            </details>}
            {(steps.length > 0 || (delegated && step.output)) && <Collapsible open={outputOpen} onOpenChange={setManualOpen} className="mt-3">
                <CollapsibleTrigger asChild>
                    <Button variant="ghost" size="sm" className="gap-2 text-body-sm text-text-2">
                        <Outlined.Down size={16} className={outputOpen ? '' : '-rotate-90'} />
                        {t('harness.memberOutput')}
                    </Button>
                </CollapsibleTrigger>
                <CollapsibleContent>
                    <MemberSteps steps={steps} running={running} />
                    {delegated && step.output && !steps.some(child => child.stepType === 'assistant' && child.output === step.output) && <div className="mt-3 text-body-sm text-text-3">
                        <p>{t('harness.result')}</p>
                        <div className="mt-2 whitespace-pre-wrap break-words border-l border-border-base pl-3 text-text-1">{step.output}</div>
                    </div>}
                </CollapsibleContent>
            </Collapsible>}
        </section>
    );
}
