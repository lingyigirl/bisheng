/**
 * F063 T-14 — the attachment bar shows ONE "all organization knowledge bases"
 * summary card, and dismissing that card means "don't use them this time"
 * rather than "forget that I ever chose".
 *
 * Why this file exists at all: the requirement behind the feature is "one card,
 * not one card per KB" (AC-17), and the remove semantics are the one place where
 * a plausible implementation is silently wrong (AC-16 坑 4 — dropping the marker
 * makes the state "never set" again, so the fallback answers `all` and the card
 * the user just dismissed is back on the next render).
 *
 * Every assertion is aimed at a *positive* artifact:
 *  - case 1/3 count the summary cards and the whole card row, so a per-KB
 *    expansion (the thing the feature exists to prevent) turns them red;
 *  - case 4 reads the tier through `resolveOrgScope` and additionally asserts the
 *    marker is still there, so implementing removal as a marker delete is red;
 *  - case 2/5 pin the two states in which no card may appear.
 * Reading the tier through the production helpers (rather than through the
 * attribute the card happens to render) is deliberate: the card disappearing is
 * necessary but not sufficient — the *state* has to say `none`.
 */

import { useState } from 'react';
import { render, screen, within } from '@testing-library/react';
import { fireEvent } from '@testing-library/react';
import { AttachmentBar } from './AttachmentBar';
import type { OrgScope } from '~/store/modeltype';
import {
  ORG_SCOPE_MARKER_ID,
  hasKnowledgeSpaces,
  isOrgScopeMarker,
  removeSelectedOrgKb,
  resolveOrgScope,
} from '~/utils/knowledgeScope';

const mockUseGetOrgToolList = jest.fn();

// The summary card is shown only when the user can reach at least one org KB
// (AC-15); the bar answers that with the same cheap existence probe the picker
// uses (design 决策 12) — page_size 1, existence only.
jest.mock('~/hooks/queries/data-provider', () => ({
  useGetOrgToolList: (...args: unknown[]) => mockUseGetOrgToolList(...args),
}));

jest.mock('~/hooks', () => ({
  useLocalize: () => (key: string) => key,
}));

// jsdom has no layout and implements neither `Element.scrollTo` nor `scrollBy`;
// the bar calls both (keep-newest-in-view, paging arrows). Without them every
// render throws inside an effect.
beforeAll(() => {
  if (!Element.prototype.scrollTo) {
    Element.prototype.scrollTo = jest.fn();
  }
  if (!Element.prototype.scrollBy) {
    Element.prototype.scrollBy = jest.fn();
  }
});

type TierItem = { id: string; name: string; type: string; orgScope?: OrgScope };

const space = (id: string): TierItem => ({ id, name: `SPACE-${id}`, type: 'space' });
const marker = (orgScope: OrgScope): TierItem => ({
  id: ORG_SCOPE_MARKER_ID,
  name: '',
  type: 'org',
  orgScope,
});
/** The probe is asked for one item; the count stands for "how many exist". */
const orgKbs = (count: number) =>
  Array.from({ length: count }, (_, i) => ({ id: i + 1, name: `KB-${i + 1}` }));

/**
 * Stands in for `AiChatInput` (`:367-382`): the bar is controlled, and a removal
 * flows back up through a single handler. The handler body is the *production*
 * helper — the harness owns only the state, so case 4 pins the real removal
 * semantics instead of the test's own arithmetic.
 */
const Bar = ({ initial, enabled = true }: { initial: TierItem[]; enabled?: boolean }) => {
  const [kbs, setKbs] = useState<TierItem[]>(initial);
  return (
    <>
      <AttachmentBar
        uploadingFiles={[]}
        files={[]}
        kbs={kbs}
        skills={[]}
        orgKbEnabled={enabled}
        onRemoveKb={(kb) => setKbs((prev) => removeSelectedOrgKb(prev, kb))}
      />
      {/* The state as the rest of the app reads it (payload / picker / hydrate),
          not as the bar happens to render it. */}
      <span data-testid="scope">{resolveOrgScope(kbs, hasKnowledgeSpaces(kbs))}</span>
      <span data-testid="marker">{String(kbs.some(isOrgScopeMarker))}</span>
      {/* Bracketed so this probe never collides with a card's own label when
          the suite looks a card up by its text. */}
      <span data-testid="spaces">
        {kbs
          .filter((i) => i.type === 'space')
          .map((i) => `[${i.name}]`)
          .join('')}
      </span>
    </>
  );
};

const renderBar = (initial: TierItem[], kbCount: number, enabled = true) => {
  mockUseGetOrgToolList.mockReturnValue({ data: orgKbs(kbCount), isFetching: false });
  return render(<Bar initial={initial} enabled={enabled} />);
};

/**
 * The summary card is found by its own label — `useLocalize` is identity here,
 * so the label *is* the key. No production data-attribute was added for this:
 * the label is already unique among the cards.
 */
const SUMMARY_LABEL = 'com_chat_knowledge_scope_all_card';
const summaryCards = () => screen.queryAllByText(SUMMARY_LABEL);
/** Every removable card in the row — spaces, files, skills *and* the summary.
 *  `queryAll` so the zero case is an assertion rather than a thrown error. */
const removeButtons = () => screen.queryAllByRole('button', { name: 'Remove' });
/** The card carrying `label` — the label span sits directly inside its shell. */
const cardFor = (label: string) => screen.getByText(label).closest('div')!;
const scope = () => screen.getByTestId('scope').textContent;

describe('AttachmentBar org-KB summary card (F063 T-14)', () => {
  it('1. shows exactly one summary card for the all-tier (AC-15, AC-02 fallback)', async () => {
    // The default state — nothing picked, so the tier falls back to `all`.
    renderBar([], 3);

    expect(await screen.findByText(SUMMARY_LABEL)).toBeTruthy();
    expect(summaryCards()).toHaveLength(1);
    // One card = one remove affordance. A per-KB expansion (or the marker
    // rendered as a kb card of its own, name and all) would add more.
    expect(removeButtons()).toHaveLength(1);
    expect(scope()).toBe('all');
  });

  it('2. shows no card for the none-tier, nor when the user has no org KB (AC-15)', async () => {
    // Every "0" here sits next to a control that differs in exactly ONE input
    // and shows the card — a bar that never rendered one at all would otherwise
    // satisfy this whole case.
    const cases = [
      { note: 'all + KBs (control)', items: [space('7'), marker('all')], kbCount: 5, cards: 1 },
      { note: 'none + KBs', items: [space('7'), marker('none')], kbCount: 5, cards: 0 },
      { note: 'all + no KB', items: [marker('all')], kbCount: 0, cards: 0 },
      { note: 'fallback all + no KB', items: [] as TierItem[], kbCount: 0, cards: 0 },
      { note: 'fallback all + KBs (control)', items: [] as TierItem[], kbCount: 5, cards: 1 },
    ];

    for (const c of cases) {
      const { unmount } = renderBar(c.items, c.kbCount);
      expect(c.note + ' → ' + summaryCards().length).toBe(c.note + ' → ' + c.cards);
      expect(screen.queryByText('KB-1')).toBeNull();
      if (c.items.some((i) => i.type === 'space')) {
        // The row really is mounted and the space card is there — "no summary
        // card" is an absence, not an empty bar.
        expect(screen.getByText('SPACE-7')).toBeTruthy();
      }
      unmount();
    }
  });

  it('3. keeps the card count at 1 however many KBs the user can use (AC-17)', async () => {
    for (const count of [1, 20, 200]) {
      const { unmount } = renderBar([], count);

      expect(summaryCards()).toHaveLength(1);
      expect(removeButtons()).toHaveLength(1);
      // The reason the feature exists: no per-KB card ever reaches the row.
      expect(screen.queryByText('KB-1')).toBeNull();
      expect(screen.queryByText(`KB-${count}`)).toBeNull();

      unmount();
    }
  });

  it('4. dismissing the card means "do not use", not "forget my choice" (AC-16)', async () => {
    renderBar([space('7'), marker('all')], 5);
    // Anchors the "0" below: the card was there to be dismissed.
    expect(summaryCards()).toHaveLength(1);

    // Scoped to the card *root*: the label span and the remove button are
    // siblings inside it.
    fireEvent.click(within(cardFor(SUMMARY_LABEL)).getByRole('button', { name: 'Remove' }));

    expect(scope()).toBe('none');
    // The marker survives as an *explicit* `none`. Deleting it instead would
    // read as "never set" and the fallback would answer `all` again — the card
    // would come straight back.
    expect(screen.getByTestId('marker').textContent).toBe('true');
    expect(summaryCards()).toHaveLength(0);
    expect(screen.getByTestId('spaces').textContent).toBe('[SPACE-7]');
  });

  it('4b. shows no card when the org-KB feature is switched off (AC-18)', async () => {
    // Same inputs as case 1's control, with only the admin switch flipped — the
    // tier stays `all` in the state (决策 13) but nothing claims to be searched.
    renderBar([], 5, false);

    expect(summaryCards()).toHaveLength(0);
    expect(removeButtons()).toHaveLength(0);
    expect(screen.getByTestId('scope').textContent).toBe('all');
  });

  it('5. knowledge-space cards behave as before (regression)', async () => {
    renderBar([space('7'), space('9')], 0);

    expect(screen.getByText('SPACE-7')).toBeTruthy();
    expect(screen.getByText('SPACE-9')).toBeTruthy();
    expect(removeButtons()).toHaveLength(2);

    fireEvent.click(within(cardFor('SPACE-7')).getByRole('button', { name: 'Remove' }));

    expect(screen.getByTestId('spaces').textContent).toBe('[SPACE-9]');
    expect(screen.queryByText('SPACE-7')).toBeNull();
  });
});
