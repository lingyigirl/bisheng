import type { ExecStepEventData } from '~/components/Linsight/Execution/execTypes';

type TaskHistory = {
    status?: string;
    history?: ExecStepEventData[];
    children?: TaskHistory[];
};

/** Project a durable terminal event, identically for live delivery and replay. */
export function closeTerminatedSteps(steps: ExecStepEventData[], stoppedAt: number): ExecStepEventData[] {
    const open = new Map<string, ExecStepEventData>();
    for (const step of steps) {
        if (!step.call_id) continue;
        if (step.status === 'end') open.delete(step.call_id);
        else open.set(step.call_id, step);
    }
    if (!open.size || !Number.isFinite(stoppedAt)) return steps;
    return [...steps, ...Array.from(open.values(), step => ({
        ...step,
        status: 'end',
        timestamp: Math.max(stoppedAt, step.timestamp ?? stoppedAt),
        output: '',
        extra_info: { ...step.extra_info, job_status: 'killed', outcome: 'cancelled' },
    }))];
}

export function closeTerminatedTasks<T extends TaskHistory>(tasks: T[], stoppedAt: number): T[] {
    return tasks.map(task => ({
        ...task,
        status: task.status === 'in_progress' ? 'terminated' : task.status,
        history: closeTerminatedSteps(task.history || [], stoppedAt),
        children: closeTerminatedTasks(task.children || [], stoppedAt),
    }));
}

export function terminationTime(terminatedAt: string | undefined, eventId?: string): number {
    // Redis stream IDs are wall-clock milliseconds, unlike MessageData.timestamp
    // (process monotonic time). They also cover older events without an ISO date.
    // Older servers emit naive local ISO dates. Prefer the timezone-independent
    // stream ID for those, so viewing from another timezone keeps the same span.
    const isoTime = terminatedAt && /(?:Z|[+-]\d{2}:\d{2})$/.test(terminatedAt)
        ? Date.parse(terminatedAt) / 1000 : NaN;
    const streamTime = Number(eventId?.split('-')[0]) / 1000;
    if (Number.isFinite(isoTime)) return isoTime;
    if (Number.isFinite(streamTime)) return streamTime;
    // Legacy WebSocket frames have no stream ID; retain their server date.
    return terminatedAt ? Date.parse(terminatedAt) / 1000 : NaN;
}
