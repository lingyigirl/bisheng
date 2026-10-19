import { memo } from 'react';
import Markdown from '~/components/Chat/Messages/Content/Markdown';
import { useLocalize } from '~/hooks';
import type { MergedStep } from './execTypes';

interface AssistantTextProps {
    step: MergedStep;
    showSpeaker?: boolean;
}

/** Assistant prose is visible independently of the collapsed reasoning panel. */
export const AssistantText = memo(function AssistantText({ step, showSpeaker = true }: AssistantTextProps) {
    const t = useLocalize();
    if (!step.output) return null;
    return (
        <div data-assistant-step={step.callId} className="min-w-0 break-words text-body text-text-1">
            {showSpeaker && <div className="mb-1 text-caption font-medium text-text-3">{String(step.extraInfo.agent_name || t('harness.mainAgent'))}</div>}
            {step.running
                ? <div className="whitespace-pre-wrap">{step.output}</div>
                : <Markdown content={step.output} isLatestMessage={false} webContent={null} />}
        </div>
    );
}, (previous, next) => previous.showSpeaker === next.showSpeaker && previous.step.extraInfo.agent_name === next.step.extraInfo.agent_name && previous.step.callId === next.step.callId &&
    previous.step.running === next.step.running && previous.step.output === next.step.output);
