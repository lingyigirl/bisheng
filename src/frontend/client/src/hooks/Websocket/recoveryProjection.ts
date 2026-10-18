type RecoveryStep = {
    call_id?: string;
    step_type?: string;
    status?: string;
    execution_epoch?: number;
    is_completed?: boolean;
    output?: string | null;
    timestamp?: number;
    started_at?: number;
    extra_info?: { invocation_id?: string };
};

const indexes = new WeakMap<RecoveryStep[], Map<string, { index: number; epoch: number }>>();

function indexSteps(steps: RecoveryStep[]) {
    let index = indexes.get(steps);
    if (!index) {
        index = new Map();
        steps.forEach((step, position) => {
            if (step.call_id) index!.set(step.call_id, { index: position, epoch: step.execution_epoch ?? 0 });
        });
        indexes.set(steps, index);
    }
    return index;
}

/** Keep completed evidence, discarding partial generation and unfinished calls. */
export function keepCompletedBeforeRecovery<T extends RecoveryStep>(steps: T[]): T[] {
    const completed = new Set(steps.filter(step => step.status === 'end').map(step => step.call_id));
    return steps.filter(step => (step.step_type === 'call_user_input' && step.is_completed) ||
        (step.step_type !== 'thinking' && !!step.call_id && completed.has(step.call_id)));
}

/** Resumed calls replace earlier attempts; chunks within one attempt append. */
export function appendRecoveryStep<T extends RecoveryStep>(steps: T[], next: T, inPlace = false): T[] {
    const epoch = next.execution_epoch ?? 0;
    const index = indexSteps(steps);
    const prior = next.call_id ? index.get(next.call_id) : undefined;
    let result = inPlace ? steps : steps.slice();
    if (prior && prior.epoch < epoch) {
        result = result.filter(step => step.call_id !== next.call_id || (step.execution_epoch ?? 0) >= epoch);
        indexes.delete(result);
    } else if (prior && prior.epoch === epoch && next.step_type === 'thinking' &&
        result[prior.index].step_type === 'thinking') {
        const previous = result[prior.index];
        result[prior.index] = { ...previous, ...next,
            output: (previous.output || '') + (next.output || ''),
            started_at: previous.started_at ?? previous.timestamp,
        };
        indexes.set(result, index);
        return result;
    }
    const nextIndex = inPlace && result === steps ? index : new Map(indexSteps(result));
    if (next.call_id) nextIndex.set(next.call_id, { index: result.length, epoch });
    result.push(next);
    indexes.set(result, nextIndex);
    return result;
}
