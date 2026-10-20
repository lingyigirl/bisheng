import { mergeTextOutput } from './textOutput';
import { createViewBatcher } from './viewBatcher';
import { appendRecoveryStep } from './recoveryProjection';

describe('assistant text projection', () => {
    it('reconciles deltas, snapshots and empty terminal frames', () => {
        const step = { call_id: 'a', step_type: 'assistant', status: 'start', output: 'Hello ', extra_info: { text_mode: 'delta' } };
        let result = appendRecoveryStep([], step);
        result = appendRecoveryStep(result, { ...step, output: 'world' });
        result = appendRecoveryStep(result, { ...step, output: 'Hello world!', extra_info: { text_mode: 'snapshot' } });
        result = appendRecoveryStep(result, { ...step, status: 'end', output: '', extra_info: { text_mode: 'end' } });
        expect(result).toHaveLength(1);
        expect(result[0].output).toBe('Hello world!');
        expect(result[0].status).toBe('end');
        expect(mergeTextOutput('a', { output: '\n', extra_info: { text_mode: 'delta' } })).toBe('a\n');
    });
    it('batches updates against the latest state and flushes a terminal barrier', () => {
        jest.useFakeTimers();
        let state = { text: '' };
        const commit = jest.fn((patch: (current: typeof state) => Partial<typeof state>) => {
            state = { ...state, ...patch(state) };
        });
        const batch = createViewBatcher(commit);
        batch.enqueue(current => ({ text: current.text + 'a' }));
        batch.enqueue(current => ({ text: current.text + 'b' }));
        expect(commit).not.toHaveBeenCalled();
        jest.advanceTimersByTime(100);
        expect(state.text).toBe('ab');
        expect(commit).toHaveBeenCalledTimes(1);
        batch.enqueue(current => ({ text: current.text + 'c' })); batch.flush();
        expect(state.text).toBe('abc');
        jest.runAllTimers();
        expect(commit).toHaveBeenCalledTimes(2);
        jest.useRealTimers();
    });
    it('ignores stale execution epochs', () => {
        const current = [{ call_id: 'a', step_type: 'assistant', execution_epoch: 2, output: 'new' }];
        expect(appendRecoveryStep(current, { ...current[0], execution_epoch: 1, output: 'old' })).toBe(current);
    });
    it('does not reopen a completed body when a late delta arrives', () => {
        const completed: Parameters<typeof appendRecoveryStep>[0] = [{ call_id: 'a', step_type: 'assistant', status: 'end', output: 'done' }];
        expect(appendRecoveryStep(completed, { ...completed[0], status: 'start', output: 'late', extra_info: { text_mode: 'delta' } })).toBe(completed);
    });
});
