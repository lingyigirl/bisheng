import { useMemo } from 'react';
import { AssistantText } from './AssistantText';
import { DeepStepGroup } from './DeepStepGroup';
import type { MergedStep } from './execTypes';
import { buildTimelineGroups } from './stepTree';

interface MemberStepsProps {
    steps: MergedStep[];
    running: boolean;
}

/** Split at prose boundaries so tools and text retain their order within a member. */
export function MemberSteps({ steps, running }: MemberStepsProps) {
    const nodes = useMemo(() => buildTimelineGroups(steps.map(step => ({
        ...step, namespace: null, extraInfo: { ...step.extraInfo, invocation_id: undefined },
    }))), [steps]);
    return <div className="mt-3 flex min-w-0 flex-col gap-3">
        {nodes.map((node, index) => {
            if (node.kind === 'step' && node.step.stepType === 'assistant') {
                return <AssistantText key={node.step.callId} step={node.step} showSpeaker={false} />;
            }
            if (node.kind === 'deep_step_group') {
                return <DeepStepGroup key={node.steps[0].callId} group={node} active={running && index === nodes.length - 1} />;
            }
            return null;
        })}
    </div>;
}
