import { buildFlowNodes, mergeStepFrames } from './stepTree';

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

test('parallel background namespaces remain separate after main work and configured delegation', () => {
    const nodes = buildFlowNodes(mergeStepFrames([
        { call_id: 'launch', step_type: 'subagent', name: 'general-purpose', status: 'start' },
        { call_id: 'a1', step_type: 'assistant', output: 'A starts', extra_info: { namespace: 'tools:A' } },
        { call_id: 'wait', step_type: 'tool', name: 'job_output', status: 'start' },
        { call_id: 'b1', step_type: 'assistant', output: 'B starts', extra_info: { namespace: 'tools:B' } },
        { call_id: 'configured', step_type: 'subagent', extra_info: { invocation_id: 'configured', agent_id: 'reviewer' } },
        { call_id: 'a2', step_type: 'assistant', output: 'A continues', extra_info: { namespace: 'tools:A' } },
        { call_id: 'b2', step_type: 'assistant', output: 'B continues', extra_info: { namespace: 'tools:B' } },
    ]));
    const members = nodes.filter(node => node.kind === 'subagent_group').flatMap(node => node.agents);
    expect(members.map(agent => [agent.step.callId, ...agent.children.map(step => step.callId)])).toEqual([
        ['a1', 'a2'], ['b1', 'b2'], ['configured'],
    ]);
    expect(nodes.filter(node => node.kind === 'step').map(node => node.step.callId)).toEqual(['wait']);
});
