import { act, renderHook } from '@testing-library/react';
import type { AgentConfig, SavedMember } from '~/types/harness';
import { useMemberDraft } from './useMemberDraft';

const member: SavedMember = {
    id: 2, name: 'Default', description: '', system_prompt: '', model_id: null,
    skill_names: [], tool_refs: [], connector_refs: [], is_default: true,
};
const snapshot: AgentConfig = {
    schema_version: 1,
    subagents: [{ ...member, id: 'original', name: 'Original' }],
};

test('late locked snapshot replaces cached defaults, including an empty snapshot', () => {
    const { result, rerender } = renderHook(
        ({ config }: { config?: AgentConfig }) => useMemberDraft('new', config, true, [member]),
        { initialProps: { config: undefined as AgentConfig | undefined } },
    );
    expect(result.current[0].subagents[0].name).toBe('Default');
    rerender({ config: snapshot });
    expect(result.current[0]).toEqual(snapshot);
    rerender({ config: { schema_version: 1, subagents: [] } });
    expect(result.current[0].subagents).toEqual([]);
});

test('existing conversation never uses library defaults while its snapshot loads', () => {
    const { result, rerender } = renderHook(
        ({ scope, config }: { scope: string; config?: AgentConfig }) => useMemberDraft(scope, config, true, [member]),
        { initialProps: { scope: 'first', config: snapshot as AgentConfig | undefined } },
    );
    expect(result.current[0]).toEqual(snapshot);
    rerender({ scope: 'second', config: undefined });
    expect(result.current[0].subagents).toEqual([]);
    rerender({ scope: 'second', config: { schema_version: 1, subagents: [{ ...snapshot.subagents[0], name: 'Second' }] } });
    expect(result.current[0].subagents[0].name).toBe('Second');
});

test('explicit deselection survives library refresh and equivalent server snapshots', () => {
    const { result, rerender } = renderHook(
        ({ config }: { config?: AgentConfig }) => useMemberDraft('new', config, true, [member]),
        { initialProps: { config: snapshot as AgentConfig | undefined } },
    );
    act(() => result.current[1]({ schema_version: 1, subagents: [] }));
    rerender({ config: JSON.parse(JSON.stringify(snapshot)) as AgentConfig });
    expect(result.current[0].subagents).toEqual([]);
    rerender({ config: undefined });
    expect(result.current[0].subagents).toEqual([]);
});
