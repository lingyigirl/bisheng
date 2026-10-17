/**
 * F087: pure helpers mapping saved-library rows to task-time members.
 *
 * A draft member sourced from the library carries the row id in its ``id``
 * (``lib-<rowId>``) — that link is how a checkbox toggle finds its member
 * again across re-renders, and how an edit can be written back to the row.
 * Pure and dependency-free so the mapping stays unit-testable.
 */
import type { AgentConfig, SavedMember, SubagentConfig } from '~/types/harness';

export const savedDraftId = (memberId: number) => `lib-${memberId}`;

export const isSavedDraft = (member: SubagentConfig) => member.id.startsWith('lib-');

export const savedIdOfDraft = (member: SubagentConfig): number | null => {
    if (!member.id.startsWith('lib-')) return null;
    const parsed = Number(member.id.slice(4));
    return Number.isFinite(parsed) ? parsed : null;
};

export const draftFromSaved = (row: SavedMember): SubagentConfig => ({
    id: savedDraftId(row.id),
    name: row.name,
    description: row.description,
    system_prompt: row.system_prompt,
    model_id: row.model_id,
    skill_names: [...row.skill_names],
    tool_refs: [...row.tool_refs],
    connector_refs: [...row.connector_refs],
});

/**
 * The default-carry set as a task config, or ``null`` when nothing is marked.
 * Capped at the backend's 5-member limit so a runaway library can never build
 * a config the submission schema would reject.
 */
export const defaultDraftFromLibrary = (rows: SavedMember[]): AgentConfig | null => {
    const defaults = rows.filter((row) => row.is_default);
    if (!defaults.length) return null;
    return { schema_version: 1, subagents: defaults.slice(0, 5).map(draftFromSaved) };
};
