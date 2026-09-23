/**
 * F063 (review H1') — the landing page must WIRE its tier into the submission.
 *
 * Why a source assertion and not a render, following the precedent of
 * `components/Chat/ChatView.test.tsx` (F063 T-09) and
 * `pages/appChat/ChatInput.wiring.test.tsx`: this component renders the whole
 * daily `AiChatInput` (icons, the `+` menu, the tools picker, bsConfig queries),
 * and the assertion would have to be driven through that render just to reach a
 * `useCallback`. Every mock added for it is a reason for this file to break for
 * an unrelated cause.
 *
 * The links on either side are asserted where they are decidable: `isOrgScopeAll`
 * in `~/utils/knowledgeScope.test.ts`, and the wire payload in
 * `~/hooks/useLinsightManager.test.tsx`. The link between them is a single line —
 * and deleting it silently returns the landing page to a tier that changes
 * nothing about what is searched, which no other test would notice.
 *
 * Two traps guarded here, both one-liners someone could plausibly write:
 *  1. deriving the flag from the array instead of the tier — `length > 0` (or a
 *     `type === 'org'` scan) reports 「不使用」 as "a KB was picked", because the
 *     tier marker's `type` IS `'org'` (design 坑 13),
 *  2. setting the flag from the execution-state input too. That input continues a
 *     session whose ids were fixed at its first submit; expanding again would
 *     widen the round to every permitted KB (design AC-25).
 */

import { readFileSync } from 'fs';
import { join } from 'path';

const LANDING = join(__dirname, 'TaskModeChatInput.tsx');
const EXECUTION = join(__dirname, 'TaskModeInput.tsx');

const source = (path: string) => readFileSync(path, 'utf-8');

describe('TaskModeChatInput org-tier wiring (F063 H1\')', () => {
  it('carries the resolved tier into the submission as the all-knowledge flag', () => {
    const text = source(LANDING);

    expect(text).toMatch(/organizationKnowledgeAll:\s*orgScopeAll\b/);
  });

  it('derives that tier from isOrgScopeAll rather than from the array', () => {
    const text = source(LANDING);

    // Used...
    expect(text).toMatch(/const orgScopeAll = isOrgScopeAll\(/);
    // ...and imported from the module that exports it. A bare call with no
    // import would be an unresolved reference (or, in a `@ts-strict-ignore`
    // file, silently undefined at runtime).
    expect(text).toMatch(/import \{[^}]*isOrgScopeAll[^}]*\} from '~\/utils\/knowledgeScope'/);
    // The trap: a raw length count would report the tier marker as a picked KB.
    expect(text).not.toMatch(/organizationKnowledgeAll:\s*[^,\n]*\.length/);
  });

  it('is set by the landing page only, never by the execution-state input', () => {
    const text = source(EXECUTION);

    // AC-25: the execution input continues an existing session, whose scope was
    // resolved at its first submit. A flag here would re-expand every round.
    expect(text).not.toMatch(/organizationKnowledgeAll/);
  });
});
