/**
 * F086 context display: token snapshot of one task run, as served by
 * GET /api/v1/linsight/workbench/context-usage.
 *
 * Token numbers are the provider's REAL usage_metadata; the breakdown block is
 * a character-ratio estimate (proportions only).
 */
export interface ContextUsageSnapshot {
    main?: {
        calls?: number;
        cum_input_tokens?: number;
        cum_output_tokens?: number;
        last_input_tokens?: number | null;
        last_output_tokens?: number | null;
    };
    sub?: { calls?: number; cum_input_tokens?: number; cum_output_tokens?: number };
    breakdown?: { system_chars?: number; history_chars?: number; tool_schema_chars?: number } | null;
}
