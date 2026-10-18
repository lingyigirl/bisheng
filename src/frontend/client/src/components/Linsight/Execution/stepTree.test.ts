import { mergeStepFrames } from './stepTree';

test('compacted thinking keeps its original duration and complete text', () => {
    const [step] = mergeStepFrames([{
        call_id: 'model', step_type: 'thinking', status: 'end',
        started_at: 10, timestamp: 41, output: 'full text',
    }]);
    expect(step.startedAt).toBe(10);
    expect(step.endedAt).toBe(41);
    expect(step.running).toBe(false);
    expect(step.output).toBe('full text');
});
