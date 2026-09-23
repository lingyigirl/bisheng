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
