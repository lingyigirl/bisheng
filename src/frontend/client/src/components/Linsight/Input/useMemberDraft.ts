import { useEffect, useState } from 'react';
import type { AgentConfig, SavedMember } from '~/types/harness';
import { defaultDraftFromLibrary } from './collaboratorUtils';

const emptyConfig = (): AgentConfig => ({ schema_version: 1, subagents: [] });

/** Scope editable selections to a conversation; server snapshots outrank defaults. */
export function useMemberDraft(
    scope: string,
    snapshot: AgentConfig | null | undefined,
    taskMode: boolean,
    savedMembers: SavedMember[] | undefined,
) {
    // Strip resolver-only fields before submitting the snapshot again.
    const canonical = snapshot
        ? { schema_version: snapshot.schema_version, subagents: snapshot.subagents } : undefined;
    const signature = canonical ? JSON.stringify(canonical) : undefined;
    const [state, setState] = useState(() => ({
        scope, signature, value: canonical ?? emptyConfig(), initialized: !!canonical,
    }));
    let current = state;
    if (state.scope !== scope || (signature !== undefined && state.signature !== signature)) {
        current = { scope, signature, value: canonical ?? emptyConfig(), initialized: !!canonical };
        // Reset during render so neither the picker nor Send can use the old conversation.
        setState(current);
    }
    useEffect(() => {
        if (!taskMode || scope !== 'new' || savedMembers === undefined) return;
        setState((previous) => {
            if (previous.scope !== scope || previous.initialized) return previous;
            return {
                ...previous,
                initialized: true,
                value: defaultDraftFromLibrary(savedMembers) ?? emptyConfig(),
            };
        });
    }, [scope, taskMode, savedMembers]);
    const handleChange = (value: AgentConfig) => {
        setState({ ...current, value, initialized: true });
    };
    return [current.value, handleChange] as const;
}
