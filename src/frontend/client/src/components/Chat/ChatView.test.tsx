/**
 * F063 T-09 — ChatView must WIRE the knowledge-scope normalisation.
 *
 * Why this is a source assertion and not a render, following the precedent of
 * `pages/appChat/ChatInput.wiring.test.tsx`:
 *
 * - `ChatView` pulls in react-activation, the router, Recoil, react-query, the
 *   linsight manager, toast/portal providers and ~15 hooks/components. Render
 *   scaffolding would be a dozen mocks, and every one of them is a reason for
 *   this file to break for a cause unrelated to the feature.
 * - The behaviour that matters is already asserted where it is decidable:
 *   `~/utils/knowledgeScope.test.ts` covers the three-state evaluation, the
 *   legacy-archive normalisation and the persist round-trip, and mutation
 *   testing proved those assertions bite.
 *
 * What cannot be asserted there is the wiring: a component that keeps its own
 * copy of the logic, or that re-adds the old filter, passes every unit test of
 * the helpers while destroying the user's tier in production. That is exactly
 * what this file pins down.
 *
 * The three traps it guards, all of them one-liners someone could plausibly add:
 *  1. reading the archive WITHOUT normalising it (legacy org entries come back),
 *  2. `type !== 'org'` — the org-KB-disabled filter that used to be right here.
 *     The tier marker's `type` IS `'org'` (design 坑 13), so re-adding that line
 *     would silently and permanently delete the user's tier on the next read,
 *  3. re-adopting `bsConfig.orgKbs[].default_checked` as a default (AC-23).
 */

import { readFileSync } from 'fs';
import { join } from 'path';

const CHAT_VIEW = join(__dirname, 'ChatView.tsx');

const source = () => readFileSync(CHAT_VIEW, 'utf-8');

describe('ChatView knowledge-scope wiring (F063 T-09)', () => {
  it('normalises the archive it reads off localStorage', () => {
    const text = source();

    // Used...
    expect(text).toMatch(/normalizeStoredItems\s*\(/);
    // ...and imported from the module that exports it. A bare call with no
    // import would be an unresolved reference (or, in a `@ts-strict-ignore`
    // file, silently undefined at runtime).
    expect(text).toMatch(
      /import\s*\{[^}]*\bnormalizeStoredItems\b[^}]*\}\s*from\s*['"][^'"]*knowledgeScope['"]/,
    );
  });

  it('feeds the normalised value straight into the atom', () => {
    // The normalised array must be what lands in state — not a value that is
    // computed and then dropped.
    expect(source()).toMatch(/setSelectedOrgKbs\s*\(\s*normalizeStoredItems\s*\(/);
  });

  it('does not filter org entries by type — that would delete the tier marker', () => {
    // Trap 2 (坑 13). Also covers `=== 'org'` reads: the marker and real
    // entries are told apart by id (`isOrgScopeMarker`), never by `type`.
    expect(source()).not.toMatch(/type\s*!==\s*'org'/);
    expect(source()).not.toMatch(/type\s*===\s*'org'/);
  });

  it('no longer adopts the admin default-checked org list (AC-23)', () => {
    // Trap 3: the old `saved === null` branch built org entries from that list.
    // With no archive the tier is now the fallback.
    // `orgKbs` is the config field name, and reaching that list requires it —
    // so its absence is exact for this defect. (`default_checked` is NOT a valid
    // probe here: the tools list on the same config legitimately uses it.)
    expect(source()).not.toMatch(/orgKbs/);
  });

  it('still persists the selection under the user-scoped key (AC-19)', () => {
    // The other half of the round-trip: a read-side fix that stopped writing
    // would make the tier last one navigation.
    const text = source();

    expect(text).toMatch(/localStorage\.setItem\(\s*`bs:\$\{user\.id\}:selectedOrgKbs`/);
  });
});
