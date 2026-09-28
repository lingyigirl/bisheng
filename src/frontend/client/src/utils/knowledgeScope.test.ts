/**
 * F063 — knowledge-scope evaluation (org-KB tier) as pure functions.
 *
 * Recoil is frozen (client `AGENTS.md`), so the tier cannot be a new atom: it is
 * a synthetic MARKER ITEM inside the existing `selectedOrgKbs` atom (design
 * 决策 6). Every decision therefore has to live in a Recoil-free module — which
 * is also the only way it can be unit-tested.
 *
 * The trap this file exists to pin down: the marker item's `type` is `'org'`,
 * exactly like a real organization KB entry (design 坑 13). Any code that picks
 * org entries with `type === 'org'` — or drops them with `type !== 'org'` — will
 * silently include or destroy the user's tier. `isOrgScopeMarker` is the only
 * sanctioned discriminator.
 *
 * Behaviour of the hydrate/persist path is covered in the second describe block
 * (T-09); the component wiring itself is asserted statically in
 * `components/Chat/ChatView.test.tsx` — see that file for why it is not a render.
 */

import {
  ORG_SCOPE_MARKER_ID,
  hasKnowledgeSpaces,
  hasPickedKnowledge,
  isOrgScopeAll,
  isOrgScopeMarker,
  normalizeStoredItems,
  onlyOrgScopeMarker,
  readOrgScope,
  removeSelectedOrgKb,
  resolveOrgScope,
  showsOrgKbSummary,
  toPayloadKnowledgeScope,
  withOrgScope,
  withoutOrgScopeMarker,
} from './knowledgeScope';
import type { SelectedOrgKb } from '~/store/modeltype';

const space = (id: string): SelectedOrgKb => ({ id, name: `space-${id}`, type: 'space' });
const legacyOrg = (id: string): SelectedOrgKb => ({ id, name: `org-${id}`, type: 'org' });
const marker = (scope: string): SelectedOrgKb =>
  ({ id: ORG_SCOPE_MARKER_ID, name: '', type: 'org', orgScope: scope }) as SelectedOrgKb;

// ---------------------------------------------------------------------------
// T-07 — three-state evaluation
// ---------------------------------------------------------------------------

describe('resolveOrgScope (F063 T-07)', () => {
  it('1. no marker + no spaces ⇒ all (AC-02, AC-03)', () => {
    expect(resolveOrgScope([], false)).toBe('all');
  });

  it('2. no marker + spaces ⇒ none (AC-02, AC-04)', () => {
    // The whole point of the fallback: "I only picked knowledge spaces" means
    // "I do not want the org KBs this time".
    expect(resolveOrgScope([space('7')], true)).toBe('none');
  });

  it('3. explicit all + spaces ⇒ all, no bounce-back (AC-05, AC-07)', () => {
    expect(resolveOrgScope([space('7'), marker('all')], true)).toBe('all');
  });

  it('4. explicit none ⇒ none, with and without spaces (AC-01, AC-06, AC-07)', () => {
    // An explicit choice is a decision, not a default — adding a space later
    // must not flip it back to "all".
    expect(resolveOrgScope([space('7'), marker('none')], true)).toBe('none');
    expect(resolveOrgScope([marker('none')], false)).toBe('none');
  });

  it('5. explicit all after clearing every space ⇒ still all (AC-07)', () => {
    // With a marker present the fallback is not consulted at all.
    expect(resolveOrgScope([marker('all')], false)).toBe('all');
  });

  it('6. clearing every space records no choice, so the fallback answers all (AC-08)', () => {
    // AC-08 is about the CLEARING — and `resolveOrgScope([], false)` on its own
    // is case 1 restated, because it never performs the transition it claims to
    // test. So start from the state that answers "none" and apply the very
    // removal the attachment bar applies.
    const starting = [space('7')];
    expect(resolveOrgScope(starting, true)).toBe('none');

    const cleared = removeSelectedOrgKb(starting, space('7'));

    expect(cleared).toEqual([]);
    // The load-bearing half: dropping the last space must not WRITE a "none"
    // decision. A marker left behind would freeze the fallback forever, which is
    // the opposite of what AC-08 promises (决策 8).
    expect(readOrgScope(cleared)).toBeUndefined();
    expect(resolveOrgScope(cleared, false)).toBe('all');
  });

  it('9. a marker with an illegal orgScope is treated as absent (dirty data)', () => {
    expect(readOrgScope([marker('partial')])).toBeUndefined();
    expect(resolveOrgScope([marker('partial')], true)).toBe('none');
    expect(resolveOrgScope([marker('partial')], false)).toBe('all');
  });
});

describe('readOrgScope vs resolveOrgScope (F063 T-07)', () => {
  it('readOrgScope reports "not explicitly set" as undefined', () => {
    expect(readOrgScope([space('7')])).toBeUndefined();
    expect(readOrgScope([])).toBeUndefined();
  });

  it('readOrgScope reads both explicit values off the marker', () => {
    expect(readOrgScope([marker('all')])).toBe('all');
    expect(readOrgScope([marker('none')])).toBe('none');
  });
});

describe('marker discrimination (F063 T-07, pitfall 13)', () => {
  it('isOrgScopeMarker separates the marker from a real org entry', () => {
    // Both carry type: 'org' — the id is what distinguishes them.
    expect(isOrgScopeMarker(marker('all'))).toBe(true);
    expect(isOrgScopeMarker(legacyOrg('12'))).toBe(false);
    expect(isOrgScopeMarker(space('7'))).toBe(false);
  });

  it('survives entries with a missing or wrong-typed id', () => {
    expect(isOrgScopeMarker({ id: undefined, name: 'x', type: 'org' } as unknown as SelectedOrgKb)).toBe(false);
    expect(isOrgScopeMarker({ id: 12, name: 'x', type: 'org' } as unknown as SelectedOrgKb)).toBe(false);
    expect(isOrgScopeMarker(null as unknown as SelectedOrgKb)).toBe(false);
  });
});

// ---------------------------------------------------------------------------
// T-07 — marker read/write helpers (shared by the tier switch and the card)
// ---------------------------------------------------------------------------

describe('withOrgScope / withoutOrgScopeMarker (F063 T-07)', () => {
  it('12. writing is idempotent — never two markers (anti-duplication)', () => {
    const once = withOrgScope([space('7')], 'none');
    const twice = withOrgScope(once, 'none');
    const overwritten = withOrgScope(twice, 'all');

    expect(twice.filter(isOrgScopeMarker)).toHaveLength(1);
    expect(overwritten.filter(isOrgScopeMarker)).toHaveLength(1);
    expect(resolveOrgScope(overwritten, true)).toBe('all');
  });

  it('12b. writing preserves the space entries and their order', () => {
    const items = [space('7'), space('8')];

    expect(withoutOrgScopeMarker(withOrgScope(items, 'all'))).toEqual(items);
  });

  it('13. removing the marker leaves spaces untouched, and is a no-op without one', () => {
    const items = [space('7'), marker('none'), space('8')];

    expect(withoutOrgScopeMarker(items)).toEqual([space('7'), space('8')]);
    // No marker present: an equivalent array, and crucially NOT a mutation of
    // the caller's array (this atom is read by several consumers).
    const noMarker = [space('7')];
    const result = withoutOrgScopeMarker(noMarker);
    expect(result).toEqual(noMarker);
    expect(noMarker).toHaveLength(1);
  });

  it('never mutates its input array', () => {
    const items = [space('7'), marker('all')];
    const snapshot = JSON.parse(JSON.stringify(items));

    withOrgScope(items, 'none');
    withoutOrgScopeMarker(items);

    expect(items).toEqual(snapshot);
  });
});

// ---------------------------------------------------------------------------
// review M1 — the legacy "clear the picked KBs" mutex
// ---------------------------------------------------------------------------

describe('onlyOrgScopeMarker (F063 review, pitfall 13)', () => {
  it('keeps the tier and drops every picked entry — including a real org one', () => {
    const items = [space('7'), legacyOrg('12'), marker('none')];

    const kept = onlyOrgScopeMarker(items);

    expect(kept.filter(isOrgScopeMarker)).toHaveLength(1);
    // 坑 13 from the other side: a real org entry shares the marker's `type`, so
    // a `type === 'org'` filter would keep it and re-arm the very mutex this
    // call exists to honour.
    expect(kept.some((i) => i.id === '12')).toBe(false);
    expect(kept.some((i) => i.type === 'space')).toBe(false);
  });

  it('stays empty when no tier was ever set', () => {
    // Clearing the picked KBs on a state that never recorded a decision must not
    // invent one — a marker written here would turn the fallback into a choice.
    const kept = onlyOrgScopeMarker([space('7')]);

    expect(kept).toEqual([]);
    expect(readOrgScope(kept)).toBeUndefined();
  });

  it('the legacy clear does not re-open org search for the none tier (review M1)', () => {
    // The regression this pins: clearing the whole array drops the marker, the
    // fallback finds no spaces left and answers 全部 — so dragging in a file
    // opened exactly the search the user had refused (AC-07/AC-16).
    expect(resolveOrgScope([], false)).toBe('all');

    const items = [space('7'), marker('none')];
    const kept = onlyOrgScopeMarker(items);

    expect(resolveOrgScope(kept, hasKnowledgeSpaces(kept))).toBe('none');
  });

  it('never mutates its input array', () => {
    const items = [space('7'), marker('all')];
    const snapshot = JSON.parse(JSON.stringify(items));

    onlyOrgScopeMarker(items);

    expect(items).toEqual(snapshot);
  });
});

// ---------------------------------------------------------------------------
// T-07 — request payload fragment
// ---------------------------------------------------------------------------

describe('toPayloadKnowledgeScope (F063 T-07)', () => {
  it('10. resolved all ⇒ organization_knowledge_all, and the two forms are exclusive (AC-05)', () => {
    const payload = toPayloadKnowledgeScope([space('7'), marker('all')], true, true);

    expect(payload.organization_knowledge_all).toBe(true);
    expect(payload.organization_knowledge_ids).toEqual([]);
    expect(payload.knowledge_space_ids).toEqual([7]);
  });

  it('10b. the fallback (no spaces, no marker) claims the all-tier too (AC-02)', () => {
    const payload = toPayloadKnowledgeScope([], false, true);

    expect(payload.organization_knowledge_all).toBe(true);
    expect(payload.knowledge_space_ids).toEqual([]);
  });

  it('11. feature disabled ⇒ never claims the all-tier, spaces still go (AC-18)', () => {
    // AC-18: with the admin switch off, the client must not ask the server to
    // expand. Spaces are a different subsystem and stay unaffected.
    const payload = toPayloadKnowledgeScope([space('7')], true, false);

    expect(payload.organization_knowledge_all).toBe(false);
    expect(payload.organization_knowledge_ids).toEqual([]);
    expect(payload.knowledge_space_ids).toEqual([7]);
  });

  it('explicit none ⇒ the flag is false, spaces still go', () => {
    const payload = toPayloadKnowledgeScope([space('7'), marker('none')], true, true);

    expect(payload.organization_knowledge_all).toBe(false);
    expect(payload.knowledge_space_ids).toEqual([7]);
  });

  it('sends only space ids as numbers — never the marker id (pitfall 13)', () => {
    const payload = toPayloadKnowledgeScope([space('7'), marker('all')], true, true);

    // Number('__org_scope__') is NaN: if the marker leaked into the id list it
    // would serialise as null and the server would reject the whole request.
    expect(payload.knowledge_space_ids).toEqual([7]);
    expect(payload.organization_knowledge_ids).not.toContain(ORG_SCOPE_MARKER_ID);
  });
});

// ---------------------------------------------------------------------------
// T-17 — "this turn searches all org KBs" as a single question
// ---------------------------------------------------------------------------

describe('isOrgScopeAll (F063 T-17)', () => {
  it("1. answers the tier, never the array: tier 'none' + spaces is false", () => {
    // The bug this pins: the task-mode landing asked "is anything of type 'org'
    // in the list?" (true — the marker is typed 'org') or "is the list non-empty?"
    // (true — the space). Both report org KBs as being searched at the very tier
    // the user set to 「不使用」.
    expect(isOrgScopeAll([space('7'), marker('none')], true)).toBe(false);
    expect(isOrgScopeAll([marker('none')], true)).toBe(false);
  });

  it('2. the AC-02 fallback still answers true', () => {
    expect(isOrgScopeAll([], true)).toBe(true);
    expect(isOrgScopeAll([space('7')], true)).toBe(false);
    expect(isOrgScopeAll([space('7'), marker('all')], true)).toBe(true);
  });

  it("3. the admin switch turns it off even at an explicit tier 'all' (AC-18)", () => {
    expect(isOrgScopeAll([marker('all')], false)).toBe(false);
    expect(isOrgScopeAll([], false)).toBe(false);
  });

  it('4. agrees with the payload flag in every state (one question, one answer)', () => {
    const states: SelectedOrgKb[][] = [
      [],
      [space('7')],
      [marker('all')],
      [marker('none')],
      [space('7'), marker('all')],
      [space('7'), marker('none')],
      [legacyOrg('12'), space('7')],
    ];

    for (const items of states) {
      for (const enabled of [true, false]) {
        const payload = toPayloadKnowledgeScope(items, hasKnowledgeSpaces(items), enabled);
        expect(isOrgScopeAll(items, enabled)).toBe(payload.organization_knowledge_all);
      }
    }
  });

  it('5. showsOrgKbSummary adds the existence gate, and only that (AC-15)', () => {
    // Same tier question, so it inherits every case above; the count is what the
    // card needs on top (it must never claim to search zero KBs).
    expect(showsOrgKbSummary([], true, 3)).toBe(true);
    expect(showsOrgKbSummary([], true, 0)).toBe(false);
    expect(showsOrgKbSummary([marker('none')], true, 3)).toBe(false);
    expect(showsOrgKbSummary([marker('all')], false, 3)).toBe(false);
  });
});

// ---------------------------------------------------------------------------
// T-09 (behaviour) — hydrate / persist round-trip
// ---------------------------------------------------------------------------

describe('normalizeStoredItems / hydrate round-trip (F063 T-09)', () => {
  it('1. legacy archive is normalised on read, so the disk loses the old entries', () => {
    const legacy = [legacyOrg('12'), legacyOrg('13'), space('7')];

    const hydrated = normalizeStoredItems(legacy);

    expect(hydrated).toEqual([space('7')]);
    expect(JSON.stringify(hydrated)).not.toContain('"org-12"');
  });

  it('7. legacy org entries mean nothing and leave no marker behind (AC-21)', () => {
    const hydrated = normalizeStoredItems([legacyOrg('12'), legacyOrg('13')]);

    expect(hydrated).toEqual([]);
    // No marker was invented, so this is "not explicitly set" — the fallback
    // decides, it is not silently frozen into a choice.
    expect(readOrgScope(hydrated)).toBeUndefined();
  });

  it('7b. does not mutate the array it was handed', () => {
    const legacy = [legacyOrg('12'), space('7')];
    const snapshot = JSON.parse(JSON.stringify(legacy));

    normalizeStoredItems(legacy);

    expect(legacy).toEqual(snapshot);
  });

  it('8. legacy archive: spaces kept, old org entries dropped (AC-21)', () => {
    const hydrated = normalizeStoredItems([legacyOrg('12'), space('7'), space('8')]);

    expect(hydrated).toEqual([space('7'), space('8')]);
    // With spaces and no marker the fallback is "none" — the spaces the user had
    // survive, which is what AC-21 promises.
    expect(resolveOrgScope(hydrated, hydrated.some((i) => i.type === 'space'))).toBe('none');
  });

  it('2 + 3. a disabled period suppresses the request but never the tier (AC-18)', () => {
    // Stored archive: an explicit "none" tier plus a legacy org entry.
    const raw = [marker('none'), legacyOrg('12'), space('7')];

    const hydrated = normalizeStoredItems(raw);

    // Constructive: the marker is IN the state, not merely "not applied".
    expect(hydrated.filter(isOrgScopeMarker)).toHaveLength(1);
    expect(resolveOrgScope(hydrated, true)).toBe('none');

    // "While the feature is off" has no `enabled` input at this layer, because
    // the switch gates the REQUEST and never the stored state (决策 13). So the
    // two halves are asserted against the same state rather than by calling the
    // same expression twice: with the switch off nothing is asked of the server
    // (AC-18), and the tier is still there to answer with afterwards.
    expect(toPayloadKnowledgeScope(hydrated, true, false).organization_knowledge_all).toBe(false);
    expect(readOrgScope(hydrated)).toBe('none');

    // Persist → reload → normalise IS the reopen, and the tier comes back.
    const revived = normalizeStoredItems(JSON.parse(JSON.stringify(hydrated)));
    expect(readOrgScope(revived)).toBe('none');
  });

  it('4. refresh persists the tier (AC-19)', () => {
    const state = withOrgScope([space('7')], 'none');

    // What ChatView writes to localStorage, then what it reads back.
    const roundTripped = normalizeStoredItems(JSON.parse(JSON.stringify(state)));

    expect(resolveOrgScope(roundTripped, true)).toBe('none');
    expect(roundTripped.filter(isOrgScopeMarker)).toHaveLength(1);
  });

  it('5. no archive ⇒ empty state, and the tier comes from the fallback (AC-23)', () => {
    // AC-23: the admin's `default_checked` org list is no longer adopted as the
    // user's default — the old code turned it into per-item org entries here.
    const hydrated = normalizeStoredItems(null);

    expect(hydrated).toEqual([]);
    expect(resolveOrgScope(hydrated, false)).toBe('all');
  });

  it('malformed archives are treated as "no archive" rather than crashing', () => {
    expect(normalizeStoredItems(undefined)).toEqual([]);
    expect(normalizeStoredItems('not-an-array')).toEqual([]);
    expect(normalizeStoredItems({ id: '7' })).toEqual([]);
    // Entries with no usable shape are dropped, the rest survive.
    expect(normalizeStoredItems([null, 42, { type: 'space' }, space('7')])).toEqual([space('7')]);
  });

  it('drops an entry whose type is missing entirely (old org entries had one)', () => {
    expect(normalizeStoredItems([{ id: '7', name: 'x' }, space('8')])).toEqual([space('8')]);
  });
});

// ---------------------------------------------------------------------------
// F063 review (M1) — "did the user pick any knowledge?", marker excluded
// ---------------------------------------------------------------------------

describe('hasPickedKnowledge (F063 review, pitfall 13)', () => {
  it('is false when the only entry is the tier marker', () => {
    // The 误报 this exists to prevent: `length > 0` reports a KB was picked, so
    // the task-mode input tells the server `org_knowledge_enabled: true` for a
    // user who explicitly chose 「不使用」.
    expect(hasPickedKnowledge([marker('none')])).toBe(false);
    expect(hasPickedKnowledge([marker('all')])).toBe(false);
  });

  it('is true for a real entry, including when the marker rides along', () => {
    expect(hasPickedKnowledge([space('7')])).toBe(true);
    expect(hasPickedKnowledge([space('7'), marker('all')])).toBe(true);
  });

  it('is false for an empty or unreadable selection', () => {
    expect(hasPickedKnowledge([])).toBe(false);
    expect(hasPickedKnowledge(null)).toBe(false);
    expect(hasPickedKnowledge(undefined)).toBe(false);
  });

  it('splits the state space with isOrgScopeAll', () => {
    // The two predicates answer "is knowledge in play this turn?" from opposite
    // ends: `all` reads the tier, `picked` reads the real entries. Written as a
    // table of concrete expectations rather than as `f(x) === g(x)` — the latter
    // restates the implementation and passes for any implementation.
    const cases: Array<{ items: SelectedOrgKb[]; all: boolean; picked: boolean }> = [
      // nothing picked -> the AC-02 fallback answers `all` with no marker present
      { items: [], all: true, picked: false },
      { items: [marker('all')], all: true, picked: false },
      // dismissed the card / picked only spaces -> search no org KB
      { items: [marker('none')], all: false, picked: false },
      { items: [space('7')], all: false, picked: true },
      // a space stacked on top of an explicit tier
      { items: [space('7'), marker('all')], all: true, picked: true },
      { items: [space('7'), marker('none')], all: false, picked: true },
    ];
    for (const { items, all, picked } of cases) {
      expect(isOrgScopeAll(items, true)).toBe(all);
      expect(hasPickedKnowledge(items)).toBe(picked);
    }
  });
});
