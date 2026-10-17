import { appendRecoveryStep, keepCompletedBeforeRecovery } from './recoveryProjection';

test('keeps completed tool evidence and drops partial generation on takeover', () => {
    const completed = { call_id: 'read', step_type: 'tool', status: 'end', output: 'evidence' };
    expect(keepCompletedBeforeRecovery([
        completed,
        { call_id: 'model', step_type: 'thinking', status: 'end' },
        { call_id: 'unfinished', step_type: 'tool', status: 'start' },
    ])).toEqual([completed]);
});

test('recomputed chunks replace an old attempt while same-attempt chunks append', () => {
    const old = { call_id: 'model', execution_epoch: 1, output: 'partial' };
    const first = { call_id: 'model', execution_epoch: 2, output: 'new' };
    const second = { call_id: 'model', execution_epoch: 2, output: ' answer' };
    expect(appendRecoveryStep(appendRecoveryStep([old], first), second)).toEqual([first, second]);
});

test('keeps completed child tools even if their enclosing delegation was interrupted', () => {
    const completedChild = { call_id: 'read2', status: 'end', extra_info: { invocation_id: 'second' } };
    const completedFirst = { call_id: 'read1', status: 'end', extra_info: { invocation_id: 'first' } };
    expect(keepCompletedBeforeRecovery([
        { call_id: 'first', step_type: 'subagent', status: 'start', extra_info: { invocation_id: 'first' } },
        completedFirst,
        completedChild,
    ])).toEqual([completedFirst, completedChild]);
});
