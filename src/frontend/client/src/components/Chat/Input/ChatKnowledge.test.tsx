/**
 * F063 T-12 — the organization-knowledge entry in the chat input offers exactly
 * two tiers (「全部」/「不使用」) and nothing else.
 *
 * Every assertion here is aimed at a *positive* artifact of the listing this
 * replaces, so that putting the listing back turns the case red: the old entry
 * was a search box (role `textbox`) plus a paginated, per-item checkbox list
 * (role `checkbox`, names fed by `useGetOrgToolList`). Case 1/2 are the mutation
 * guard for that; case 3 pins which tier reads as selected; case 4 pins the
 * admin switch (AC-18) and that the knowledge-space list is left alone.
 *
 * The tier evaluation itself is a pure function of its own right — covered by
 * `~/utils/knowledgeScope.test.ts`; this file only pins what the entry renders
 * and what it writes back through `onChange`.
 */

import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen } from '@testing-library/react';
import { ChatKnowledge } from './ChatKnowledge';
import type { KnowledgeItem } from './knowledgeTypes';
import type { BsConfig } from '~/types/chat';
import type { OrgScope } from '~/store/modeltype';
import { ORG_SCOPE_MARKER_ID } from '~/utils/knowledgeScope';

// Radix menus open on pointerdown, and jsdom ships neither PointerEvent nor the
// pointer-capture methods Radix probes for. Same polyfills as
// `components/ui/Select.test.tsx`.
beforeAll(() => {
  if (!window.PointerEvent) {
    Object.defineProperty(window, 'PointerEvent', {
      configurable: true,
      value: MouseEvent,
    });
  }
  if (!Element.prototype.scrollIntoView) {
    Element.prototype.scrollIntoView = jest.fn();
  }
  if (!Element.prototype.hasPointerCapture) {
    Element.prototype.hasPointerCapture = jest.fn(() => false);
  }
  if (!Element.prototype.setPointerCapture) {
    Element.prototype.setPointerCapture = jest.fn();
  }
  if (!Element.prototype.releasePointerCapture) {
    Element.prototype.releasePointerCapture = jest.fn();
  }
});

const mockUseGetOrgToolList = jest.fn();

// The entry must not request or render the org listing any more (AC-14), so the
// component no longer reaches for this hook. The mock is kept deliberately: it
// is what gives case 2 teeth — re-render the listing and `KB-1` / `KB-200`
// reappear in the panel.
jest.mock('~/hooks/queries/data-provider', () => ({
  useGetOrgToolList: (...args: unknown[]) => mockUseGetOrgToolList(...args),
}));

jest.mock('~/hooks', () => ({
  useLocalize: () => (key: string) => key,
  useMediaQuery: () => false,
  useScrollRevealRef: () => () => {},
  useCategorizedKnowledgeSpaces: () => ({
    groups: [{ key: 'mine', label: 'MINE', spaces: [{ id: '7', name: 'SPACE-7' }] }],
    isFetching: false,
  }),
}));

jest.mock('~/Providers', () => ({
  useToastContext: () => ({ showToast: jest.fn() }),
}));

/** The entry only ever reads `knowledgeBase.enabled` off the config. */
const configWith = (enabled: boolean): BsConfig =>
  ({ knowledgeBase: { enabled } } as unknown as BsConfig);

type TierItem = KnowledgeItem & { orgScope?: OrgScope };

const marker = (orgScope: OrgScope): TierItem => ({
  id: ORG_SCOPE_MARKER_ID,
  name: '',
  type: 'org',
  orgScope,
});
const space = (id: string): TierItem => ({ id, name: `SPACE-${id}`, type: 'space' });
/** The probe returns page_size=1 items out of however many exist; only the
 *  existence matters (design 决策 12), which is why the count is faked here. */
const orgKbs = (count: number) =>
  Array.from({ length: count }, (_, i) => ({ id: i + 1, name: `KB-${i + 1}` }));

const renderEntry = ({
  value = [] as TierItem[],
  config,
  variant = 'plus' as const,
}: {
  value?: TierItem[];
  config?: BsConfig;
  variant?: 'plus' | 'knowledge';
} = {}) => {
  const onChange = jest.fn();
  const utils = render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <ChatKnowledge
        variant={variant}
        config={config}
        disabled={false}
        value={value}
        onChange={onChange}
      />
    </QueryClientProvider>,
  );
  return { ...utils, onChange };
};

/** Open the picker by its trigger (portalled content → query via `screen`). */
const openMenu = (triggerLabel: string) => {
  fireEvent.pointerDown(screen.getByRole('button', { name: triggerLabel }), {
    button: 0,
    ctrlKey: false,
  });
};

const tierRows = () => Array.from(document.querySelectorAll<HTMLElement>('[data-org-scope]'));
/** The tier row itself — clicking it is what has to reach `onChange`. */
const tierRow = (scope: 'all' | 'none') => {
  const row = tierRows().find((r) => r.getAttribute('data-org-scope') === scope);
  if (!row) throw new Error(`no tier row rendered for scope=${scope}`);
  return row;
};
const selectedTier = () =>
  tierRows()
    .find((row) => row.getAttribute('data-selected') === 'true')
    ?.getAttribute('data-org-scope');

describe('ChatKnowledge org-knowledge entry (F063 T-12)', () => {
  it('1. renders exactly the two tiers and no per-KB selection control (AC-13)', async () => {
    mockUseGetOrgToolList.mockReturnValue({ data: orgKbs(1), isFetching: false });
    renderEntry();

    openMenu('com_knowledge_add_file');
    await screen.findByText('com_chat_knowledge_scope_all');

    expect(tierRows().map((row) => row.getAttribute('data-org-scope'))).toEqual(['all', 'none']);
    expect(screen.getByText('com_chat_knowledge_scope_all')).toBeTruthy();
    expect(screen.getByText('com_chat_knowledge_scope_none')).toBeTruthy();
    // The per-KB control of the old entry: gone, at every level.
    expect(screen.queryAllByRole('checkbox')).toHaveLength(0);
    expect(screen.queryByRole('menuitemcheckbox')).toBeNull();
  });

  it('2. lists no KB name and no search box, however many KBs the user can use (AC-14)', async () => {
    for (const count of [1, 200]) {
      mockUseGetOrgToolList.mockReturnValue({ data: orgKbs(count), isFetching: false });
      const { unmount } = renderEntry();

      openMenu('com_knowledge_add_file');
      await screen.findByText('com_chat_knowledge_scope_all');

      // Constructive: the panel is open (the tiers above are asserted present),
      // and neither a name from the probe nor a listing affordance is in it.
      expect(screen.queryByText(`KB-${count}`)).toBeNull();
      expect(screen.queryByText('KB-1')).toBeNull();
      expect(screen.queryByRole('textbox')).toBeNull();
      expect(screen.queryAllByRole('checkbox')).toHaveLength(0);

      unmount();
    }
  });

  it('3. marks the tier the state resolves to, explicit or fallback (AC-13, AC-02)', async () => {
    // Explicit choice wins.
    const explicitAll = renderEntry({ value: [space('7'), marker('all')] });
    openMenu('com_knowledge_add_file');
    await screen.findByText('com_chat_knowledge_scope_all');
    expect(selectedTier()).toBe('all');
    explicitAll.unmount();

    const explicitNone = renderEntry({ value: [space('7'), marker('none')] });
    openMenu('com_knowledge_add_file');
    await screen.findByText('com_chat_knowledge_scope_all');
    expect(selectedTier()).toBe('none');
    explicitNone.unmount();

    // Never set, no space picked → the fallback answers "all" (AC-02/AC-03).
    const fallbackAll = renderEntry({ value: [] });
    openMenu('com_knowledge_add_file');
    await screen.findByText('com_chat_knowledge_scope_all');
    expect(selectedTier()).toBe('all');
    fallbackAll.unmount();

    // Never set, a space picked → the fallback answers "not use" (AC-02/AC-04).
    renderEntry({ value: [space('7')] });
    openMenu('com_knowledge_add_file');
    await screen.findByText('com_chat_knowledge_scope_all');
    expect(selectedTier()).toBe('none');
  });

  it('4. hides the whole entry when the admin switch is off (AC-18)', async () => {
    renderEntry({ config: configWith(false), value: [space('7')] });

    openMenu('com_knowledge_add_file');
    // Anchor: the panel really is open and empty of org entries — not merely
    // "the trigger never opened".
    await screen.findByRole('menu');

    expect(tierRows()).toHaveLength(0);
    expect(screen.queryByText('com_chat_knowledge_scope_all')).toBeNull();
    expect(screen.queryByText('com_chat_knowledge_scope_none')).toBeNull();
  });

  it('4b. the knowledge-space list is not gated by that switch (AC-18)', async () => {
    renderEntry({ variant: 'knowledge', config: configWith(false), value: [space('7')] });

    openMenu('com_ui_knowledge_space');

    expect(await screen.findByText('SPACE-7')).toBeTruthy();
  });

  it('5. writes the chosen tier back through onChange, keeping the picked entries (AC-13)', async () => {
    const { onChange } = renderEntry({ value: [space('7')] });

    openMenu('com_knowledge_add_file');
    await screen.findByText('com_chat_knowledge_scope_all');

    fireEvent.click(tierRow('all'));

    // Assert `onChange` itself, NOT the rendered `data-selected`: that attribute
    // is derived from the `value` PROP, so it stays correct even if the row
    // writes nothing at all (case 3 would pass on a dead `onSelect`). This is
    // the only case here that would notice.
    expect(onChange).toHaveBeenCalledTimes(1);
    const written = onChange.mock.calls[0][0] as TierItem[];
    expect(written.filter((i) => i.type === 'space').map((i) => i.id)).toEqual(['7']);
    expect(written.filter((i) => i.id === ORG_SCOPE_MARKER_ID).map((i) => i.orgScope)).toEqual([
      'all',
    ]);
  });

  it('5b. switching tiers overwrites the marker instead of stacking a second one (AC-13)', async () => {
    const { onChange } = renderEntry({ value: [space('7'), marker('all')] });

    openMenu('com_knowledge_add_file');
    await screen.findByText('com_chat_knowledge_scope_none');

    fireEvent.click(tierRow('none'));

    const written = onChange.mock.calls[0][0] as TierItem[];
    const markers = written.filter((i) => i.id === ORG_SCOPE_MARKER_ID);
    expect(markers.map((i) => i.orgScope)).toEqual(['none']);
  });
});
