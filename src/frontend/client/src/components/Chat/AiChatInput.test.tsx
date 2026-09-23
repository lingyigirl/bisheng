/**
 * F063 (review M2) — the attachment strip's MOUNT GATE must count cards, not
 * array entries.
 *
 * `AttachmentBar` itself is covered by its own test; what is pinned here is
 * `AiChatInput`'s decision to mount it. That decision is the only thing that
 * decides whether the empty-strip bug can come back, and it is one boolean away
 * from the bug in both directions:
 *
 *  - counting `selectedOrgKbs.length` mounts a strip for the tier marker — a
 *    state carrier that draws no card — so dismissing the card at tier「不使用」
 *    leaves a blank strip behind (the deviation record's T-17③ fix),
 *  - dropping `showsOrgSummaryCard` from the gate hides the strip in the
 *    AC-15 default state, where the tier is 「全部」 by fallback, the array is
 *    EMPTY and the summary card is nonetheless the strip's only content.
 *
 * Why a source assertion and not a render, following the precedent of
 * `components/Chat/ChatView.test.tsx` (T-09) and
 * `components/Linsight/Input/TaskModeChatInput.test.tsx` (H1'): `AiChatInput`
 * pulls in the whole toolbar — icons, the `+` menu, the tools picker, the model
 * selector, two bsConfig/react-query reads and the drag-drop overlay. Driving
 * that render to observe one boolean would need a mock per dependency, and each
 * mock is a reason for this file to break for an unrelated cause.
 */

import { readFileSync } from 'fs';
import { join } from 'path';

const INPUT = join(__dirname, 'AiChatInput.tsx');

const source = readFileSync(INPUT, 'utf-8');

describe('AiChatInput attachment-strip mount gate (F063 M2)', () => {
  it('counts cards by dropping the tier marker rather than counting array entries', () => {
    // The marker's `type` is 'org', the same as a real entry (design 坑 13), so
    // `withoutOrgScopeMarker` — not a `type` scan — is the sanctioned filter.
    expect(source).toMatch(/const orgKbCardCount = withoutOrgScopeMarker\(selectedOrgKbs\)\.length/);
  });

  it('builds the mount gate from the card count, never from the raw array length', () => {
    expect(source).toMatch(/const hasSelectionTags = [^;]*orgKbCardCount/);
    // The trap this pins: reverting to the raw count re-mounts the blank strip.
    expect(source).not.toMatch(/const hasSelectionTags = [^;]*selectedOrgKbs\.length/);
  });

  it('admits the summary card itself as a reason to mount', () => {
    // AC-15's default state: tier「全部」by fallback + a KB exists ⇒ the card is
    // on the strip while the selection array holds no entry at all.
    expect(source).toMatch(/const hasSelectionTags = [^;]*showsOrgSummaryCard/);
  });
});

/**
 * F063 review H1/M1 — the two legacy mutexes, which the marker broke in
 * opposite directions.
 *
 * Both are `if (… && !agentMode)` blocks that were written when a
 * `selectedOrgKbs` entry could only mean "the user picked this KB". The tier
 * marker is an entry that means nothing of the sort (设计 坑 13), and it is
 * always present once a tier is set:
 *
 *  - reading `val.length > 0` therefore read 「不使用」 as "a KB was picked" and
 *    wiped the attachments the user had just added (H1);
 *  - writing `([])` threw the marker away with the picked entries, so the AC-02
 *    fallback answered again and 「不使用」 silently became 「全部」 — the search
 *    the user had refused, re-opened (M1).
 *
 * The logic under both is behavioural-tested at the pure layer
 * (`knowledgeScope.test.ts`: `onlyOrgScopeMarker`, `hasPickedKnowledge`); what
 * is pinned here is only that the call sites USE it, for the same reason the
 * cases above are source assertions — rendering this toolbar costs a mock per
 * dependency and buys nothing this regex does not already give.
 */
describe('AiChatInput legacy mutexes preserve the org tier (F063 review H1/M1)', () => {
  it('clears the picked KBs without dropping the tier marker', () => {
    // Both mutexes: "files were added" and "联网搜索 was switched on".
    const clears =
      source.match(/onSelectedOrgKbsChange\?\.\(onlyOrgScopeMarker\(selectedOrgKbs\)\)/g) ?? [];
    expect(clears).toHaveLength(2);
    // The exact regression: clearing the whole array takes the tier with it.
    expect(source).not.toMatch(/onSelectedOrgKbsChange\?\.\(\[\]\)/);
  });

  it('gates both kb mutexes on picked entries, not on the raw array length', () => {
    const gates = source.match(/if \(hasPickedKnowledge\(val\) && !agentMode\) \{/g) ?? [];
    expect(gates).toHaveLength(2);
    expect(source).not.toMatch(/if \(val\.length > 0 && !agentMode\)/);
  });
});
