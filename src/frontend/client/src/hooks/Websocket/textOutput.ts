type TextOutput = { output?: string | null; extra_info?: { text_mode?: string } | null };

/** Deltas append; authoritative snapshots replace; end frames only close. */
export function mergeTextOutput(previous: string, next: TextOutput): string {
    const mode = next.extra_info?.text_mode;
    if (mode === 'end') return previous;
    if (mode === 'snapshot') return next.output ?? '';
    return previous + (next.output ?? '');
}
