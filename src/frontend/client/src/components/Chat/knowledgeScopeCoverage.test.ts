/**
 * F063 AC-20 / AC-24 — the two coverage claims, which had no test at all.
 *
 * Both are claims about the SHAPE of the app rather than about a value, which is
 * why they cannot sit in `~/utils/knowledgeScope.test.ts` next to the pure
 * functions:
 *
 *  - AC-24 says the scope applies to every entry that reuses the question input
 *    「不因入口不同而分叉」. What makes that true is that there is exactly ONE
 *    input and exactly ONE org entry, mounted by all of them — a per-entry copy
 *    is how this breaks, and it breaks silently (the copy would pass every unit
 *    test of the helpers while reporting a different scope).
 *  - AC-20 says the subscription panel shares the state 「但不单独保存」. What
 *    makes that true is that the scope reaches localStorage from exactly one
 *    place. A second writer looks fine until a refresh has to pick a winner.
 *
 * So both are asserted by scanning `src/` for the things that would have to
 * change: a second renderer of the org entry, or a second writer of the key. A
 * scan is the only thing that can catch an ADDITION — clicking through the app
 * would only ever exercise the entry it was pointed at, which is exactly the
 * blind spot that made these two ACs look covered when they were not.
 *
 * What this file still does NOT do is exercise the four entries end to end. That
 * stays manual, and `e2e-checklist.md` lists it as UNEXECUTED rather than done.
 */

import { readdirSync, readFileSync, statSync } from 'fs';
import { join } from 'path';

const SRC = join(__dirname, '..', '..');

/** Every non-test `.ts` / `.tsx` under `src/`. */
function sourceFiles(): string[] {
  const walk = (dir: string): string[] =>
    readdirSync(dir).flatMap((entry) => {
      const full = join(dir, entry);
      if (statSync(full).isDirectory()) return walk(full);
      if (!/\.tsx?$/.test(entry) || /\.test\.tsx?$/.test(entry)) return [];
      return [full];
    });
  return walk(SRC);
}

const read = (file: string) => readFileSync(file, 'utf-8');

const filesMatching = (pattern: RegExp) => sourceFiles().filter((f) => pattern.test(read(f)));

/** Paths relative to `src/`, so a failure names the offending file readably. */
const relative = (files: string[]) =>
  files.map((f) => f.slice(SRC.length + 1).replace(/\\/g, '/'));

describe('knowledge-scope entry coverage (F063 AC-24)', () => {
  it('renders the org entry from exactly one component, so entries cannot fork', () => {
    // A fork arrives as a second `<ChatKnowledge`. The entry carries the tier UI,
    // so a copy of it is a copy of the scope decision.
    expect(relative(filesMatching(/<ChatKnowledge[\s>]/))).toEqual([
      'components/Chat/AiChatInput.tsx',
    ]);
  });

  it('is mounted by the subscription panel rather than re-implemented there', () => {
    const panel = read(join(SRC, 'pages', 'Subscription', 'AiChat', 'AiAssistantPanel.tsx'));

    expect(panel).toMatch(/<AiChatInput[\s>]/);
    // The panel legitimately holds the atom itself — it drives the same state,
    // which is AC-20's point. What it must not hold is its own org entry.
    expect(panel).not.toMatch(/<ChatKnowledge[\s>]/);
  });
});

describe('knowledge-scope persistence site (F063 AC-20)', () => {
  it('writes the scope to localStorage from exactly one place', () => {
    // A second writer is how the panel would start keeping its own copy.
    expect(relative(filesMatching(/localStorage\.setItem\(\s*`[^`]*selectedOrgKbs`/))).toEqual([
      'components/Chat/ChatView.tsx',
    ]);
  });

  it('reads it back under the same user-scoped key it writes', () => {
    // Pinning the write key alone proves nothing: a read that drifted to another
    // key finds nothing and falls back, which looks exactly like a first visit.
    // Both sides are tied to the same `bs:${user.id}:` prefix here.
    const view = read(join(SRC, 'components', 'Chat', 'ChatView.tsx'));

    expect(view).toMatch(/const prefix = `bs:\$\{user\.id\}:`/);
    expect(view).toMatch(/localStorage\.getItem\(`\$\{prefix\}selectedOrgKbs`\)/);
    expect(view).toMatch(/localStorage\.setItem\(`bs:\$\{user\.id\}:selectedOrgKbs`/);
  });
});
