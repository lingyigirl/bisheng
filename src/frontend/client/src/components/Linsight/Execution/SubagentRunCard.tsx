import type { SubagentGroup } from './execTypes';
import { useLocalize } from '~/hooks';
import { useExecutionTurnLive } from './executionLive';
import { useElapsedTicker } from './useElapsedTicker';

interface SubagentRunCardProps {
    group: SubagentGroup;
}

export function SubagentRunCard({ group }: SubagentRunCardProps) {
    const t = useLocalize();
    // Turn-level liveness, NOT the planning-phase flag: while tasks already
    // exist the planning flag is false even though this subagent is genuinely
    // running — using it here mislabeled a live delegation as "已停止".
    const live = useExecutionTurnLive();
    const agent = group.agents[0];
    const step = agent?.step;
    const running = Boolean(step && live && step.running);
    // Second-level WS timestamps → ms for the ticker. While the delegation is
    // actually running the ticker re-renders every 100ms so the counter
    // advances between frames instead of freezing at the last frame's stamp
    // (reported 2026-10-15: "正在执行 · 5s 一直是 5s"); once closed it freezes
    // at endedAt − startedAt.
    const { elapsedMs } = useElapsedTicker(
        step?.startedAt != null ? step.startedAt * 1000 : null,
        step?.endedAt != null ? step.endedAt * 1000 : null,
        running,
    );
    if (!agent || !step) return null;
    const failed = step.extraInfo.outcome === 'error';
    const stopped = step.extraInfo.job_status === 'killed' || step.running;
    const status = running ? 'harness.running' : stopped ? 'harness.stopped' : failed ? 'harness.failed' : 'harness.completed';
    const elapsed = Math.round(elapsedMs / 1000);
    return (
        <section className="rounded-xl border border-border-base bg-fill-1 p-4">
            <div className="flex items-center justify-between gap-3">
                <span className="text-body font-medium text-text-1">{String(step.extraInfo.agent_name || group.name)}</span>
                <span className="text-caption text-text-3" role="status">{t(status)}{elapsed > 0 ? ` · ${elapsed}s` : ''}</span>
            </div>
            <p className="mt-2 text-body-sm text-text-2">{String(step.extraInfo.delegate_goal || step.callReason)}</p>
            {step.output && <details className="mt-3 text-body-sm text-text-3">
                <summary className="cursor-pointer">{t('harness.result')}</summary>
                <div className="mt-2 max-h-96 overflow-auto whitespace-pre-wrap break-words border-l border-border-base pl-3 text-text-1">{step.output}</div>
            </details>}
            {agent.children.length > 0 && <details className="mt-3 text-body-sm text-text-3">
                <summary className="cursor-pointer">{t('harness.details')} · {agent.children.length}</summary>
                <ol className="mt-2 space-y-2 border-l border-border-base pl-3">
                    {agent.children.map((child) => <li key={child.callId}>
                        <p className="text-text-2">{child.callReason || child.name}</p>
                        <p className="max-h-48 overflow-auto whitespace-pre-wrap break-words">{child.output}</p>
                    </li>)}
                </ol>
            </details>}
        </section>
    );
}
