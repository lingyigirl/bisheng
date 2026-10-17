type RecoveryStep = {
    call_id?: string;
    step_type?: string;
    status?: string;
    execution_epoch?: number;
    is_completed?: boolean;
    extra_info?: { invocation_id?: string };
};

/** Keep completed evidence, discarding partial generation and unfinished calls. */
export function keepCompletedBeforeRecovery<T extends RecoveryStep>(steps: T[]): T[] {
    const completed = new Set(steps.filter(step => step.status === 'end').map(step => step.call_id));
    return steps.filter(step => (step.step_type === 'call_user_input' && step.is_completed) ||
        (step.step_type !== 'thinking' && !!step.call_id && completed.has(step.call_id)));
}

/** Resumed calls replace earlier attempts; chunks within one attempt append. */
export function appendRecoveryStep<T extends RecoveryStep>(steps: T[], next: T): T[] {
    const epoch = next.execution_epoch ?? 0;
    return [...steps.filter(step => !(next.call_id && step.call_id === next.call_id &&
        (step.execution_epoch ?? 0) < epoch)), next];
}
