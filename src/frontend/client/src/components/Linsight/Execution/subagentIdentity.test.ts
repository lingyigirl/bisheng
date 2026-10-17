import { buildFlowNodes, isTimelineNodeRunning, mergeStepFrames } from './stepTree';
import type { ExecStepEventData } from './execTypes';

test('an answered clarification intent is terminal when mixed with member activity', () => {
    expect(isTimelineNodeRunning({ kind: 'intent', data: {
        call_id: 'clarify', name: 'ask_user', step_type: 'call_user_input', status: 'end', user_input: 'A',
    } })).toBe(false);
});

test('parallel calls to the same member use invocation identity, not arrival order', () => {
    const event = (call: string, invocation: string, namespace: string | null): ExecStepEventData => ({
        call_id: call, step_type: namespace ? 'tool' : 'subagent', name: 'Research',
        status: 'end', output: call, extra_info: { namespace, agent_id: 'research', agent_name: 'Research', invocation_id: invocation },
    });
    const nodes = buildFlowNodes(mergeStepFrames([
        event('call-a', 'call-a', null), event('call-b', 'call-b', null),
        event('tool-b', 'call-b', 'ns-b'), event('tool-a', 'call-a', 'ns-a'),
    ]));
    expect(nodes).toHaveLength(2);
    if (nodes[0].kind !== 'subagent_group' || nodes[1].kind !== 'subagent_group') throw new Error('Expected collaborator groups');
    expect(nodes[0].agents[0].children.map((step) => step.callId)).toEqual(['tool-a']);
    expect(nodes[1].agents[0].children.map((step) => step.callId)).toEqual(['tool-b']);
});
