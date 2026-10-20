import { SubagentRunCard } from "./SubagentRunCard";
/**
 * ExecutionTimeline — the single aggregation entry for task-mode execution flow
 * (Wave2 / F5). Replaces StepList's bare dispatch: it walks
 * buildTimelineGroups(mergeStepFrames(history)) and renders the Wave2 timeline
 * node family (deep_step_group / subagent_group), isomorphic to the daily /c
 * "深度思考" surface. All four carrier surfaces (ExecutionFlow / TaskTurnPanel /
 * ConversationRound / TaskStepRow) collapse onto this one component so the
 * live-vs-refresh / per-task / session-scoped views can never drift.
 *
 * It does NOT import or touch any Chat/Messages (daily /c) component — the shared
 * visual tokens live in the task-mode primitives (CollapsibleTimelineItem /
 * TimelineRail / useElapsedTicker) and the Wave2 group components below.
 */
import { useMemo } from 'react';
import { AssistantText } from './AssistantText';
import { DeepStepGroup } from './DeepStepGroup';
import { useExecutionTurnLive } from './executionLive';
import { IntentRow } from './IntentRow';
import { KnowledgeRow } from './KnowledgeRow';
import { ToolRowLite } from './ToolRowLite';
import type { ExecStepEventData } from './stepUtils';
import { buildTimelineGroups, mergeStepFrames } from './stepUtils';

interface ExecutionTimelineProps {
    history: ExecStepEventData[] | null | undefined;
    finalAnswerStepId?: string;
}

export function ExecutionTimeline({ history, finalAnswerStepId }: ExecutionTimelineProps) {
    // F3 perf: building the node tree is several O(n) passes (merge + group +
    // episode aggregation). The WS pump mutates `history` in place (same array
    // reference), so the reference alone is not enough to invalidate the memo — a
    // composite signature (length + last frame's status / call_id) re-fires it on
    // each appended/closed frame while skipping the wasteful per-render rebuild.
    const frames = history || [];
    const last = frames[frames.length - 1];
    const sig = `${frames.length}:${last?.status ?? ''}:${last?.call_id ?? ''}:${last?.output?.length ?? 0}`;
    // eslint-disable-next-line react-hooks/exhaustive-deps -- intentional composite key over the in-place-mutated history array
    const nodes = useMemo(() => buildTimelineGroups(mergeStepFrames(history).filter(step => step.callId !== finalAnswerStepId)), [history, sig, finalAnswerStepId]);
    // Container liveness (session turn / running task), provided by the carrier via
    // ExecutionLiveContext. The ACTIVE episode is the LAST node while the container
    // is live — that one carries the live facets (正在 label, header pulse, ticking
    // clock); every earlier node is done. (The fold itself opens collapsed for all
    // groups — see DeepStepGroup.) Passing this stable `active` down (instead of
    // letting each group read the volatile per-tool group.running) is what stops the
    // label/pulse from flickering on every tool call within one episode.
    const live = useExecutionTurnLive();
    if (!nodes.length) return null;
    const lastIdx = nodes.length - 1;

    return (
        // Group spacing aligns with the daily /c stack (gap-3 between groups).
        <div className="flex flex-col gap-3">
            {nodes.map((node, idx) => {
                const active = live && idx === lastIdx;
                // Inline answered clarify (时序内联): an "已经明确用户意图" summary row
                // sitting between the pre-question thinking and the resumed thinking.
                // Always collapsed (no `active`) — it is a record, not a live episode.
                if (node.kind === 'intent') {
                    return <IntentRow key={`intent_${idx}`} data={node.data} />;
                }
                if (node.kind === 'deep_step_group') {
                    // Stable key: first step's callId; fall back to index when an
                    // episode somehow has no steps (defensive — flush() never emits
                    // an empty episode, so this is a belt-and-braces guard).
                    const key = node.steps[0]?.callId ?? `deep_${idx}`;
                    return <DeepStepGroup key={key} group={node} active={active} />;
                }
                if (node.kind === 'subagent_group') {
                    return node.agents.map((agent, memberIndex) => (
                        <SubagentRunCard key={String(agent.step.extraInfo.invocation_id || agent.step.namespace || agent.step.callId)}
                            group={{ ...node, agents: [agent], goals: agent.step.extraInfo.invocation_id ? [node.goals?.[memberIndex] || ''] : [] }} />
                    ));
                }
                // Residual bare `step` node: buildTimelineGroups wraps every
                // top-level step into a deep_step_group, so this branch is a
                // defensive fallback for any future node shape. thinking is
                // already absorbed by deep_step_group; everything else degrades to
                // the appropriate lite row.
                const { step } = node;
                if (step.stepType === 'assistant') return <AssistantText key={step.callId} step={step} />;
                if (step.stepType === 'knowledge') {
                    return <KnowledgeRow key={step.callId} step={step} />;
                }
                return <ToolRowLite key={step.callId} step={step} />;
            })}
        </div>
    );
}
