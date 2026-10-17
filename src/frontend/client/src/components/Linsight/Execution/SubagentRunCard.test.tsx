import '@testing-library/jest-dom';
import { act, render, screen } from '@testing-library/react';
import type { SubagentGroup } from './execTypes';
import { ExecutionTurnLiveContext } from './executionLive';
import { SubagentRunCard } from './SubagentRunCard';
import { mergeStepFrames } from './stepTree';

jest.mock('~/hooks', () => ({ useLocalize: () => (key: string) => key }));

// Partial fixture: the card only reads a handful of step fields, so the full
// ExecStep contract is deliberately stubbed (`unknown` cast keeps lint clean).
function makeGroup(stepOverrides: Record<string, unknown> = {}): SubagentGroup {
    return {
        name: 'subagent-group',
        agents: [
            {
                step: {
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
    const summary = screen.getByText('harness.result');
    const details = summary.closest('details');
    expect(details).not.toBeNull();
    expect(details).not.toHaveAttribute('open');
    expect(details).toHaveTextContent('# long report body');
});

test('replayed background completion shows the final report and actual duration', () => {
    const [step] = mergeStepFrames([
        { call_id: 'child', step_type: 'subagent', status: 'start', timestamp: 100, extra_info: { agent_name: 'Verifier' } },
        { call_id: 'child', step_type: 'subagent', status: 'end', timestamp: 195, output: 'Verified total: 400', extra_info: { job_status: 'completed', outcome: 'success' } },
    ]);
    const group = makeGroup({ ...step });
    renderWithTurnLive(false, group);
    expect(screen.getByRole('status')).toHaveTextContent('harness.completed · 95s');
    expect(screen.getByText('Verified total: 400')).toBeInTheDocument();
});

test('a cancelled background child is stopped rather than failed', () => {
    renderWithTurnLive(false, makeGroup({ running: false, extraInfo: { job_status: 'killed', outcome: 'error' } }));
    expect(screen.getByRole('status')).toHaveTextContent('harness.stopped');
    expect(screen.getByRole('status')).not.toHaveTextContent('harness.failed');
});
