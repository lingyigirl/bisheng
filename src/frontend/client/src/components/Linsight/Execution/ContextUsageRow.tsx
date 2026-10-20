/**
 * F086 context display: compact footer with the task run's REAL token usage.
 *
 * The token numbers come from the provider's ``usage_metadata`` (folded per
 * model call by the resilience middleware): ``tokens`` is the newest MAIN call's
 * input == current context size; the cumulative input/output sum every call.
 * The composition tooltip is a character-ratio ESTIMATE (system prompt /
 * conversation / tool schemas) — labelled as such, it shows proportions, never
 * fake token precision.
 *
 * Hidden until the run has made at least one main-graph call.
 */
import { Gauge } from 'lucide-react';
import { useLocalize } from '~/hooks';
import type { ContextUsageSnapshot } from '~/types/contextUsage';

/**
 * Compact token units — the row is a glanceable status line, not a ledger:
 * ``999 → "999"``, ``10,082 → "10.1K"``, ``128,000 → "128K"``,
 * ``1,234,567 → "1.2M"``. One decimal below 100 units, none at/above; a value
 * that would render as ``1000K`` is promoted to M so that never appears.
 */
export function formatTokenCount(value: number | null | undefined): string {
    const n = Number(value) || 0;
    const abs = Math.abs(n);
    if (abs < 1_000) return String(Math.round(n));
    const thousands = trimUnit(n / 1_000);
    // Rounded 999.6K would print as "1000K" — promote to M instead.
    if (Number(thousands) < 1_000) return `${thousands}K`;
    return `${trimUnit(n / 1_000_000)}M`;
}

function trimUnit(scaled: number): string {
    const text = Math.abs(scaled) >= 100 ? scaled.toFixed(0) : scaled.toFixed(1);
    return text.endsWith('.0') ? text.slice(0, -2) : text;
}

function share(part: number, total: number): number {
    return total > 0 ? Math.round((part / total) * 100) : 0;
}

export function ContextUsageRow({ usage }: { usage?: ContextUsageSnapshot | null }) {
    const localize = useLocalize();
    const main = usage?.main;
    if (!main || (main.calls ?? 0) <= 0) return null;

    const breakdown = usage?.breakdown;
    const totalChars = breakdown
        ? (breakdown.system_chars ?? 0) + (breakdown.history_chars ?? 0) + (breakdown.tool_schema_chars ?? 0)
        : 0;
    const detail =
        breakdown && totalChars > 0
            ? localize('com_linsight_context_breakdown', {
                  system: share(breakdown.system_chars ?? 0, totalChars),
                  history: share(breakdown.history_chars ?? 0, totalChars),
                  tools: share(breakdown.tool_schema_chars ?? 0, totalChars),
              })
            : undefined;

    return (
        <div className="mt-2 flex items-center gap-1.5 text-caption text-text-3" title={detail} data-testid="context-usage">
            <Gauge size={13} className="shrink-0" />
            <span className="truncate">
                {localize('com_linsight_context_summary', {
                    tokens: formatTokenCount(main.last_input_tokens),
                    input: formatTokenCount(main.cum_input_tokens),
                    output: formatTokenCount(main.cum_output_tokens),
                    calls: main.calls ?? 0,
                    sub: usage?.sub?.calls ?? 0,
                })}
            </span>
        </div>
    );
}
