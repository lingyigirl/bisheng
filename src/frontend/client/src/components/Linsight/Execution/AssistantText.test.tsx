import '@testing-library/jest-dom';
import { render, screen } from '@testing-library/react';
import { AssistantText } from './AssistantText';
import { SubagentRunCard } from './SubagentRunCard';
import { mergeStepFrames } from './stepTree';
import { ExecutionTurnLiveContext } from './executionLive';

jest.mock('~/hooks', () => ({ useLocalize: () => (key: string) => key }));
jest.mock('./useElapsedTicker', () => ({ useElapsedTicker: () => ({ elapsedMs: 0 }) }));
jest.mock('./DeepStepGroup', () => ({ DeepStepGroup: () => null }));
jest.mock('~/components/Chat/Messages/Content/Markdown', () => ({
    __esModule: true,
    default: ({ content }: { content: string }) => <p>{content}</p>,
}));

function assistant(output: string, status = 'start') {
    return mergeStepFrames([{ call_id: 'body', task_id: 'task', step_type: 'assistant', status, output }])[0];
}

test('body updates during generation and accepts a snapshot of the same length', () => {
    const { rerender } = render(<AssistantText step={assistant('first')} />);
    expect(screen.getByText('first')).toBeVisible();
    rerender(<AssistantText step={assistant('other')} />);
    expect(screen.getByText('other')).toBeVisible();
    rerender(<AssistantText step={assistant('other', 'end')} />);
    expect(screen.getByText('other').tagName).toBe('P');
});

test('collaborator prose remains outside the collapsed tool details', () => {
    const member = mergeStepFrames([{ call_id: 'member', task_id: 'task', step_type: 'subagent', status: 'start',
        name: 'Reviewer', extra_info: { agent_id: 'reviewer', agent_name: 'Reviewer' } }])[0];
    render(<ExecutionTurnLiveContext.Provider value><SubagentRunCard group={{ kind: 'subagent_group', name: 'Reviewer', agents: [{ step: member, children: [assistant('Visible member prose')] }] }} /></ExecutionTurnLiveContext.Provider>);
    expect(screen.getByText('Visible member prose')).toBeVisible();
    expect(screen.getByText('Visible member prose').closest('details')).toBeNull();
});
