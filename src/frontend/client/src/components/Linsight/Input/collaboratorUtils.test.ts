import type { SavedMember } from '~/types/harness';
import { defaultDraftFromLibrary, draftFromSaved, isSavedDraft, savedDraftId, savedIdOfDraft } from './collaboratorUtils';

const base: SavedMember = {
    id: 1,
    name: 'M',
    description: 'd',
    system_prompt: 'p',
    model_id: null,
    skill_names: [],
    tool_refs: [],
    connector_refs: [],
    is_default: false,
    issues: {},
};

test('maps library rows to drafts and back by id', () => {
    const draft = draftFromSaved({ ...base, id: 7 });
    expect(draft.id).toBe('lib-7');
    expect(isSavedDraft(draft)).toBe(true);
    expect(savedIdOfDraft(draft)).toBe(7);
    expect(savedIdOfDraft({ ...draft, id: 'agent-x' })).toBeNull();
    expect(savedIdOfDraft({ ...draft, id: 'lib-not-a-number' })).toBeNull();
    expect(savedDraftId(9)).toBe('lib-9');
});

test('default seed keeps only starred rows and caps at five', () => {
    const rows = [1, 2, 3, 4, 5, 6, 7].map((id) => ({ ...base, id, is_default: id % 2 === 1 }));
    const seeded = defaultDraftFromLibrary(rows);
    expect(seeded?.subagents.map((member) => member.id)).toEqual(['lib-1', 'lib-3', 'lib-5', 'lib-7']);

    const many = [1, 2, 3, 4, 5, 6, 7, 8, 9].map((id) => ({ ...base, id, is_default: true }));
    expect(defaultDraftFromLibrary(many)?.subagents).toHaveLength(5);

    expect(defaultDraftFromLibrary([{ ...base, is_default: false }])).toBeNull();
});
