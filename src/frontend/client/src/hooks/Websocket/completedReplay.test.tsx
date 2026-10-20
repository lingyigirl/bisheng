import { act, renderHook, waitFor } from '@testing-library/react';
import { getReplaySnapshot } from '~/api/harness';
import { useLinsightWebSocket } from './index';

jest.mock('~/api/harness', () => ({ getReplaySnapshot: jest.fn() }));
jest.mock('~/hooks', () => ({ useLocalize: () => (key: string) => key }));
jest.mock('~/Providers', () => ({ useToastContext: () => ({ showToast: jest.fn() }) }));
jest.mock('~/utils', () => ({ toggleNav: jest.fn() }));
jest.mock('~/api/linsight', () => ({
    getLinsightSessionVersionList: jest.fn(), userInputLinsightEvent: jest.fn(), userStopLinsightEvent: jest.fn(),
}));
jest.mock('./mock', () => ({ MockWebSocket: jest.fn() }));
jest.mock('~/store/linsight', () => ({ SopStatus: { Running: 'running', completed: 'completed' } }));

type TestState = {
    status: string;
    tasks: unknown[];
    sessionSteps: Record<string, unknown>[];
    agent_config?: { event_protocol?: number; subagents: { id: string }[] };
    output_result?: { answer: string };
};
let mockState: TestState;
const mockUpdate = jest.fn((_id: string, update: Partial<TestState> | ((state: TestState) => Partial<TestState>)) => {
    mockState = { ...mockState, ...(typeof update === 'function' ? update(mockState) : update) };
});
jest.mock('../useLinsightManager', () => ({
    useLinsightManager: () => ({ getLinsight: () => mockState, updateLinsight: mockUpdate }),
    mapSessionVersionStatus: (status: string) => status,
    buildTaskTree: (tasks: unknown[]) => tasks,
}));

beforeEach(() => {
    jest.clearAllMocks();
    mockState = { status: 'completed', tasks: [], sessionSteps: [{ output: 'truncated' }] };
});

test.each([0, 1])('completed Harness turn with %i members rebuilds history once without a socket', async (count) => {
    mockState.agent_config = { event_protocol: 2, subagents: Array.from({ length: count }, () => ({ id: 'member' })) };
    const socket = jest.spyOn(window, 'WebSocket');
    jest.mocked(getReplaySnapshot).mockResolvedValue({
        legacy: false, terminal: true, complete_history: true, last_event_id: '2-0',
        snapshot: { events: [{ event_id: '1-0', event_type: 'task_execute_step', data: { task_id: 'task', output: 'full text' } }] },
        version: { status: 'completed', agent_config: mockState.agent_config },
    });
    const { rerender } = renderHook(() => useLinsightWebSocket('task'));
    await waitFor(() => expect(mockState.sessionSteps).toEqual([{ task_id: 'task', output: 'full text' }]));
    rerender();
    expect(getReplaySnapshot).toHaveBeenCalledTimes(1);
    expect(socket).not.toHaveBeenCalled();
    socket.mockRestore();
});

test('member presence does not upgrade a historical task without the replay protocol', () => {
    mockState.agent_config = { subagents: [{ id: 'old-member' }] };
    renderHook(() => useLinsightWebSocket('historical'));
    expect(getReplaySnapshot).not.toHaveBeenCalled();
});

test('large snapshot commits projected history once rather than updating state per token', async () => {
    mockState.agent_config = { event_protocol: 2, subagents: [] };
    jest.mocked(getReplaySnapshot).mockResolvedValue({
        legacy: false, terminal: true, complete_history: true, last_event_id: '20000-0',
        snapshot: { events: Array.from({ length: 20000 }, (_, i) => ({
            event_id: `${i + 1}-0`, event_type: 'task_execute_step', data: {
                task_id: 'task', call_id: 'model', step_type: 'thinking', output: 'x',
            },
        })) },
        version: { status: 'completed', agent_config: mockState.agent_config },
    });
    renderHook(() => useLinsightWebSocket('task'));
    await waitFor(() => expect(mockState.sessionSteps[0]?.output).toBe('x'.repeat(20000)));
    expect(mockState.sessionSteps).toHaveLength(1);
    expect(mockUpdate.mock.calls.filter(([, update]) => typeof update !== 'function' && 'sessionSteps' in update))
        .toHaveLength(1);
    expect(mockUpdate.mock.calls.length).toBeLessThan(6);
});

test('legacy completed turns and shared turns do not request private replay', () => {
    renderHook(() => useLinsightWebSocket('legacy'));
    mockState.agent_config = { event_protocol: 2, subagents: [{ id: 'member' }] };
    renderHook(() => useLinsightWebSocket(''));
    expect(getReplaySnapshot).not.toHaveBeenCalled();
});

test('uses committed result when worker died before publishing the final frame', async () => {
    mockState.agent_config = { event_protocol: 2, subagents: [{ id: 'member' }] };
    jest.mocked(getReplaySnapshot).mockResolvedValue({
        legacy: false, terminal: true, complete_history: true, last_event_id: '1-0',
        snapshot: { events: [] },
        version: { status: 'completed', agent_config: mockState.agent_config, output_result: { answer: 'committed result' } },
    });
    renderHook(() => useLinsightWebSocket('task'));
    await waitFor(() => expect(mockState.output_result).toEqual({ answer: 'committed result' }));
});

test('reloading a recovered task removes old partial output before replaying the new attempt', async () => {
    mockState.agent_config = { event_protocol: 2, subagents: [{ id: 'member' }] };
    jest.mocked(getReplaySnapshot).mockResolvedValue({
        legacy: false, terminal: true, complete_history: true, last_event_id: '3-0',
        snapshot: { events: [
            { event_id: '1-0', event_type: 'task_execute_step', execution_epoch: 1,
                data: { task_id: 'task', call_id: 'model', step_type: 'thinking', output: 'partial' } },
            { event_id: '2-0', event_type: 'task_recovery', data: { epoch: 2, attempt: 1 } },
            { event_id: '3-0', event_type: 'task_execute_step', execution_epoch: 2,
                data: { task_id: 'task', call_id: 'model', step_type: 'thinking', output: 'recomputed' } },
        ] },
        version: { status: 'completed', agent_config: mockState.agent_config },
    });
    renderHook(() => useLinsightWebSocket('task'));
    await waitFor(() => expect(mockState.sessionSteps).toEqual([
        { task_id: 'task', call_id: 'model', step_type: 'thinking', output: 'recomputed', execution_epoch: 2 },
    ]));
});

test('another subscriber and a refreshed page recover the persisted clarify answer', async () => {
    mockState.agent_config = { event_protocol: 2, subagents: [{ id: 'member' }] };
    const question = { task_id: 'task', step_type: 'call_user_input', params: {} };
    jest.mocked(getReplaySnapshot).mockResolvedValue({
        legacy: false, terminal: true, complete_history: true, last_event_id: '2-0',
        snapshot: { events: [
            { event_id: '1-0', event_type: 'user_input', data: question },
            { event_id: '2-0', event_type: 'user_input_completed', data: {
                id: 'task', status: 'user_input_completed',
                history: [{ ...question, user_input: 'A', is_completed: true }],
            } },
        ] },
        version: { status: 'completed', agent_config: mockState.agent_config },
    });
    renderHook(() => useLinsightWebSocket('task'));
    await waitFor(() => expect(mockState.sessionSteps).toEqual([
        { ...question, user_input: 'A', is_completed: true },
    ]));
});

test('a running task keeps retrying after a long outage and cancels retries on unmount', async () => {
    jest.useFakeTimers();
    mockState.status = 'running';
    mockState.agent_config = { event_protocol: 2, subagents: [{ id: 'member' }] };
    jest.mocked(getReplaySnapshot).mockResolvedValue({
        legacy: false, terminal: false, complete_history: true, last_event_id: '1-0',
        snapshot: { events: [] }, version: { status: 'running', agent_config: mockState.agent_config },
    });
    const sockets: { onclose?: () => void; close: jest.Mock }[] = [];
    const socketSpy = jest.spyOn(window, 'WebSocket').mockImplementation(() => {
        const socket = { close: jest.fn() };
        sockets.push(socket);
        return socket as unknown as WebSocket;
    });
    const { unmount } = renderHook(() => useLinsightWebSocket('retry-task'));
    await act(async () => { await Promise.resolve(); });
    expect(sockets).toHaveLength(1);
    for (const delay of [1000, 2000, 4000, 8000, 16000, 30000, 30000]) {
        const count = sockets.length;
        act(() => sockets.at(-1)?.onclose?.());
        await act(async () => { await jest.advanceTimersByTimeAsync(delay); });
        expect(sockets).toHaveLength(count + 1);
    }
    act(() => sockets.at(-1)?.onclose?.());
    unmount();
    await act(async () => { await jest.advanceTimersByTimeAsync(60000); });
    expect(sockets).toHaveLength(8);
    socketSpy.mockRestore();
    jest.useRealTimers();
});

test('snapshot transport failures retry while active and stop when the task finishes', async () => {
    jest.useFakeTimers();
    const errorLog = jest.spyOn(console, 'error').mockImplementation(() => undefined);
    mockState.status = 'running';
    jest.mocked(getReplaySnapshot).mockRejectedValue(new Error('API unavailable'));
    const { rerender, unmount } = renderHook(() => useLinsightWebSocket('snapshot-retry'));
    await act(async () => { await Promise.resolve(); });
    await act(async () => { await jest.advanceTimersByTimeAsync(15000); });
    expect(getReplaySnapshot).toHaveBeenCalledTimes(5);
    mockState.status = 'completed';
    rerender();
    await act(async () => { await jest.advanceTimersByTimeAsync(60000); });
    expect(getReplaySnapshot).toHaveBeenCalledTimes(5);
    unmount();
    errorLog.mockRestore();
    jest.useRealTimers();
});
