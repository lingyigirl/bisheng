import '@testing-library/jest-dom';
import { act, fireEvent, render, screen } from '@testing-library/react';
import type { SubagentGroup } from './execTypes';
import { ExecutionTurnLiveContext } from './executionLive';
import { SubagentRunCard } from './SubagentRunCard';
import { mergeStepFrames } from './stepTree';

jest.mock('~/hooks', () => ({ useLocalize: () => (key: string) => key }));
jest.mock('./DeepStepGroup', () => ({ DeepStepGroup: () => <div>Tool activity</div> }));
jest.mock('~/components/Chat/Messages/Content/Markdown', () => ({
    __esModule: true, default: ({ content }: { content: string }) => <p>{content}</p>,
}));

// Partial fixture: the card only reads a handful of step fields, so the full
// ExecStep contract is deliberately stubbed (`unknown` cast keeps lint clean).
function makeGroup(stepOverrides: Record<string, unknown> = {}): SubagentGroup {
    return {
        name: 'subagent-group',
        agents: [
            {
                step: {
                    stepType: 'subagent',
                    running: true,
                    // Second-level epoch timestamps, matching the WS frames.
                    startedAt: 1_759_000_000,
                    endedAt: 1_759_000_006,
                    extraInfo: { agent_name: 'researcher' },
                    output: '',
                    callReason: '',
                    ...stepOverrides,
                },
                children: [],
            },
        ],
    } as unknown as SubagentGroup;
}

function renderWithTurnLive(live: boolean, group: SubagentGroup) {
    return render(
        <ExecutionTurnLiveContext.Provider value={live}>
            <SubagentRunCard group={group} />
        </ExecutionTurnLiveContext.Provider>,
    );
}

test('a running subagent reads the TURN-live flag, not the planning-phase one', () => {
    // Regression (2026-10-14/15): the card was gated on ExecutionLiveContext,
    // which is false whenever tasks already exist (planning is over) — a live
    // delegation in the session-level timeline then rendered "已停止 · Ns".
    // With the turn-live context true and step.running true it must read running.
    // Fake "now" so the live clock starts exactly 6s in.
    jest.useFakeTimers();
    jest.setSystemTime(new Date('2026-10-15T03:00:00Z'));
    const nowS = Math.floor(Date.now() / 1000);
    renderWithTurnLive(true, makeGroup({ startedAt: nowS - 6, endedAt: nowS }));
    expect(screen.getByRole('status')).toHaveTextContent('harness.running · 6s');
    jest.useRealTimers();
});

test('member prose keeps its chronological position around tool activity', () => {
    const children = mergeStepFrames([
        { call_id: 'one', step_type: 'assistant', output: 'Before tools', status: 'end' },
        { call_id: 'tool', step_type: 'tool', name: 'web_search', status: 'end' },
        { call_id: 'two', step_type: 'assistant', output: 'After tools', status: 'start' },
    ]);
    const group = makeGroup();
    group.agents[0].children = children;
    const { container } = renderWithTurnLive(true, group);
    const text = container.textContent || '';
    expect(text.indexOf('Before tools')).toBeLessThan(text.indexOf('Tool activity'));
    expect(text.indexOf('Tool activity')).toBeLessThan(text.indexOf('After tools'));
    expect(screen.getByText('After tools').closest('details')).toBeNull();
});

test('the running clock advances between frames', () => {
    // Regression (2026-10-15): the counter used to freeze at the last frame's
    // timestamp; while a delegation runs it must tick on its own.
    jest.useFakeTimers();
    jest.setSystemTime(new Date('2026-10-15T03:00:00Z'));
    const nowS = Math.floor(Date.now() / 1000);
    renderWithTurnLive(true, makeGroup({ startedAt: nowS - 5, endedAt: nowS }));
    expect(screen.getByRole('status')).toHaveTextContent('harness.running · 5s');

    act(() => {
        jest.advanceTimersByTime(3000);
    });

    expect(screen.getByRole('status')).toHaveTextContent('harness.running · 8s');
    jest.useRealTimers();
});

test('a dangling step after the turn ended collapses to stopped', () => {
    renderWithTurnLive(false, makeGroup());
    expect(screen.getByRole('status')).toHaveTextContent('harness.stopped · 6s');
});

test('a finished step shows completed regardless of turn liveness', () => {
    renderWithTurnLive(true, makeGroup({ running: false }));
    expect(screen.getByRole('status')).toHaveTextContent('harness.completed · 6s');
});

test('the subagent result is collapsed behind a summary by default', () => {
    // Regression (2026-10-15): a long markdown result expanded inline made the
    // conversation unscrollable; it must start folded.
    renderWithTurnLive(true, makeGroup({ running: false, output: '# long report body' }));
    const toggle = screen.getByRole('button', { name: 'harness.memberOutput' });
    expect(toggle).toHaveAttribute('aria-expanded', 'false');
    expect(screen.queryByText('# long report body')).not.toBeInTheDocument();
    fireEvent.click(toggle);
    expect(screen.getByText('# long report body')).toBeVisible();
    fireEvent.click(toggle);
    expect(screen.queryByText('# long report body')).not.toBeInTheDocument();
});

test('replayed background completion shows the final report and actual duration', () => {
    const [step] = mergeStepFrames([
        { call_id: 'child', step_type: 'subagent', status: 'start', timestamp: 100, extra_info: { agent_name: 'Verifier' } },
        { call_id: 'child', step_type: 'subagent', status: 'end', timestamp: 195, output: 'Verified total: 400', extra_info: { job_status: 'completed', outcome: 'success' } },
    ]);
    const group = makeGroup({ ...step });
    renderWithTurnLive(false, group);
    expect(screen.getByRole('status')).toHaveTextContent('harness.completed · 95s');
    fireEvent.click(screen.getByRole('button', { name: 'harness.memberOutput' }));
    expect(screen.getByText('Verified total: 400')).toBeInTheDocument();
});

test('the streamed final answer can fold even when the duplicate result is omitted', () => {
    const group = makeGroup({ running: false, output: 'Final member answer' });
    group.agents[0].children = mergeStepFrames([
        { call_id: 'answer', step_type: 'assistant', output: 'Final member answer', status: 'end' },
    ]);
    renderWithTurnLive(false, group);
    expect(screen.queryByText('Final member answer')).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'harness.memberOutput' }));
    expect(screen.getAllByText('Final member answer')).toHaveLength(1);
    expect(screen.getByText('Final member answer')).toBeVisible();
});

test('live output opens by default and folds on completion without a manual choice', () => {
    const group = makeGroup();
    group.agents[0].children = mergeStepFrames([{ call_id: 'answer', step_type: 'assistant', output: 'Live answer' }]);
    const { rerender } = renderWithTurnLive(true, group);
    expect(screen.getByText('Live answer')).toBeVisible();
    rerender(<ExecutionTurnLiveContext.Provider value={false}><SubagentRunCard group={group} /></ExecutionTurnLiveContext.Provider>);
    expect(screen.getByRole('button', { name: 'harness.memberOutput' })).toHaveAttribute('aria-expanded', 'false');
    expect(screen.queryByText('Live answer')).not.toBeInTheDocument();
});

test('a manual expansion survives completion and each member folds independently', () => {
    const group = makeGroup({ output: 'Member answer' });
    const { rerender } = render(<ExecutionTurnLiveContext.Provider value><SubagentRunCard group={group} /><SubagentRunCard group={makeGroup({ running: false, output: 'Other answer' })} /></ExecutionTurnLiveContext.Provider>);
    fireEvent.click(screen.getAllByRole('button', { name: 'harness.memberOutput' })[0]);
    fireEvent.click(screen.getAllByRole('button', { name: 'harness.memberOutput' })[0]);
    rerender(<ExecutionTurnLiveContext.Provider value={false}><SubagentRunCard group={makeGroup({ running: false, output: 'Member answer' })} /><SubagentRunCard group={makeGroup({ running: false, output: 'Other answer' })} /></ExecutionTurnLiveContext.Provider>);
    expect(screen.getByText('Member answer')).toBeVisible();
    expect(screen.queryByText('Other answer')).not.toBeInTheDocument();
});

test('a cancelled background child is stopped rather than failed', () => {
    renderWithTurnLive(false, makeGroup({ running: false, extraInfo: { job_status: 'killed', outcome: 'error' } }));
    expect(screen.getByRole('status')).toHaveTextContent('harness.stopped');
    expect(screen.getByRole('status')).not.toHaveTextContent('harness.failed');
});
