import '@testing-library/jest-dom';
import { render, screen } from '@testing-library/react';
import { useLinsightWebSocket } from '~/hooks/Websocket';
import { useLinsightQueuePolling } from '~/hooks/useLinsightQueuePolling';
import { TaskTurnPanel } from './TaskTurnPanel';

let mockParams: { token?: string } = {};
let mockRecoveryAttempt = 0;
let mockRecoveryBlocked = false;
jest.mock('react-router-dom', () => ({ useParams: () => mockParams }));
jest.mock('~/hooks', () => ({ useLocalize: () => (key: string) => key }));
jest.mock('~/hooks/queries/data-provider', () => ({ useGetBsConfig: () => ({ data: {} }) }));
jest.mock('~/hooks/useAutoScroll', () => ({ useAutoScroll: jest.fn() }));
jest.mock('~/hooks/useLinsightQueuePolling', () => ({ useLinsightQueuePolling: jest.fn() }));
jest.mock('~/hooks/Websocket', () => ({ useLinsightWebSocket: jest.fn(() => ({ sendInput: jest.fn(), stop: jest.fn() })) }));
jest.mock('~/api/linsight', () => ({
    getLinsightSessionVersionList: jest.fn(),
    getLinsightTaskList: jest.fn(),
    // F086 context row: the hook fires on mount; resolve with an empty snapshot.
    getLinsightContextUsage: jest.fn(() => Promise.resolve({ data: {} })),
}));
jest.mock('~/store/linsight', () => ({ SopStatus: { Running: 'running', completed: 'completed', Stoped: 'stopped' } }));
jest.mock('~/hooks/useLinsightManager', () => ({ useLinsightManager: () => ({
    getLinsight: () => ({ status: 'running', queueCount: 2, tasks: [], sessionSteps: [], agent_config: { subagents: [{ id: 'member' }] }, recoveryAttempt: mockRecoveryAttempt, taskErrorInfo: mockRecoveryBlocked ? { error_type: 'recovery_blocked' } : undefined }),
    switchAndUpdateLinsight: jest.fn(), updateLinsight: jest.fn(),
}) }));
jest.mock('~/components/Linsight/Input/SubagentPicker', () => ({ SubagentPicker: () => <button>Inspect configuration</button> }));
jest.mock('~/components/Linsight/Artifacts/ResultSection', () => ({ ResultSection: () => null }));
jest.mock('~/components/ChatErrorCard', () => ({ ChatErrorCard: () => null }));
jest.mock('./BreathingRow', () => ({ BreathingRow: () => null }));
jest.mock('./ClarifyCard', () => ({ ClarifyCard: () => null }));
jest.mock('./ExecutionTimeline', () => ({ ExecutionTimeline: () => null }));
jest.mock('./ResultPanel', () => ({ ResultPanel: () => null }));
jest.mock('./TaskStepRow', () => ({ TaskStepRow: () => null }));
jest.mock('./QueueCard', () => ({ QueueCard: () => <button>Cancel queued task</button> }));

beforeEach(() => { jest.clearAllMocks(); mockParams = {}; mockRecoveryAttempt = 0; mockRecoveryBlocked = false; });

test('blocked recovery never claims that automatic continuation succeeded', () => {
    mockRecoveryAttempt = 1;
    const { rerender } = render(<TaskTurnPanel versionId="task" conversationId="conversation" />);
    expect(screen.getByText('harness.recoveryAttempt')).toBeInTheDocument();
    mockRecoveryBlocked = true;
    rerender(<TaskTurnPanel versionId="task" conversationId="conversation" />);
    expect(screen.queryByText('harness.recoveryAttempt')).not.toBeInTheDocument();
});

test('share route is read-only even when the parent omits the readOnly prop', () => {
    mockParams = { token: 'test-share' };
    render(<TaskTurnPanel versionId="task" conversationId="conversation" />);
    expect(useLinsightWebSocket).toHaveBeenCalledWith('');
    expect(useLinsightQueuePolling).toHaveBeenCalledWith('task', false);
    expect(screen.queryByRole('button', { name: 'Inspect configuration' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Cancel queued task' })).not.toBeInTheDocument();
});

test('private owner view retains configuration inspection and queue cancellation', () => {
    render(<TaskTurnPanel versionId="task" conversationId="conversation" />);
    expect(useLinsightWebSocket).toHaveBeenCalledWith('task');
    expect(screen.getByRole('button', { name: 'Inspect configuration' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Cancel queued task' })).toBeInTheDocument();
});
