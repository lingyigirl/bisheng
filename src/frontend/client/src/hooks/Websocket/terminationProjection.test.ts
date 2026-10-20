import { mergeStepFrames } from '~/components/Linsight/Execution/stepTree';
import { closeTerminatedSteps, closeTerminatedTasks, terminationTime } from './terminationProjection';

test('terminal replay closes delegation and pending tools at the durable stop time', () => {
    const completed = { call_id: 'done', status: 'end', timestamp: 105, output: 'evidence' };
    const frames = [
        { call_id: 'member', status: 'start', timestamp: 100, step_type: 'subagent' },
        { call_id: 'wait', status: 'start', timestamp: 101, name: 'job_output' },
        completed,
    ];
    const closed = closeTerminatedSteps(frames, 131);
    const merged = mergeStepFrames(closed);
    expect(merged.every(step => !step.running)).toBe(true);
    expect(merged[0].endedAt! - merged[0].startedAt!).toBe(31);
    expect(merged[1].extraInfo.job_status).toBe('killed');
    expect(closed[2]).toBe(completed);
    expect(closeTerminatedSteps(closed, 999)).toBe(closed);
    expect(frames).toHaveLength(3);
});

test('closes nested task histories and preserves existing completed evidence', () => {
    const tasks = closeTerminatedTasks([{ status: 'in_progress', children: [{ history: [
        { call_id: 'code', status: 'start', timestamp: 10 },
    ] }] }], 40);
    expect(tasks[0].status).toBe('terminated');
    expect(tasks[0].children[0].history.at(-1)?.timestamp).toBe(40);
});

test('uses an ISO date or stream ID, never the process monotonic timestamp', () => {
    expect(terminationTime('2026-10-18T00:09:45Z', '1000-0')).toBe(1792282185);
    expect(terminationTime(undefined, '1792282185000-0')).toBe(1792282185);
    expect(terminationTime('2026-10-18T08:09:45', '1792282185000-0')).toBe(1792282185);
    expect(Number.isNaN(terminationTime(undefined))).toBe(true);
});
