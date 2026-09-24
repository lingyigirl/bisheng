/**
 * F065 T-06 / T-07 — the two-pane knowledge-space file browser (AC-01, AC-02,
 * AC-03, AC-05, AC-06, AC-29, AC-30, AC-31, AC-32).
 *
 * Assertions go through `screen`, never `container`: Radix renders the dialog in
 * a portal, so a "did not render" check against the render container is true by
 * construction and would pass even if the row were there (F064 T-13's lesson).
 *
 * jest-dom matchers (`toBeInTheDocument` & co) are avoided: `tsconfig.json` puts
 * `types` outside `compilerOptions`, so their types never load and they fail
 * `tsc-strict`; new files may not add the `@ts-strict-ignore` header that legacy
 * tests use to opt out. `getBy*` throws when absent, so plain assertions are
 * equivalent.
 */

import { fireEvent, render, screen } from '@testing-library/react';
import type { ComponentProps } from 'react';
import { FileType, type KnowledgeFile, type KnowledgeSpace } from '~/api/knowledge';
import { KnowledgeFilePicker } from './index';

const mockUseCategorizedSpaces = jest.fn();
const mockUseSpaceChildren = jest.fn();

jest.mock('~/hooks/useCategorizedKnowledgeSpaces', () => ({
  useCategorizedKnowledgeSpaces: (...args: unknown[]) => mockUseCategorizedSpaces(...args),
}));

// The paging/filter helpers stay real — the picker renders their output.
jest.mock('./useKnowledgeBrowse', () => ({
  ...jest.requireActual('./useKnowledgeBrowse'),
  useSpaceChildren: (...args: unknown[]) => mockUseSpaceChildren(...args),
}));

// The debounce is a timer; these cases assert the keyword reaching the hook, so
// the wait would only add flakiness.
jest.mock('~/hooks/Input/useDebounce', () => ({
  __esModule: true,
  default: (value: string) => value,
}));

// `useLocalize` reads a Recoil atom, and every Recoil import is frozen
// (client `AGENTS.md`). These cases assert testids, not copy, so a passthrough
// stub keeps the picker renderable without dragging a RecoilRoot in.
jest.mock('~/hooks/useLocalize', () => ({
  __esModule: true,
  default: () => (key: string, options?: Record<string, unknown>) =>
    options ? `${key}:${JSON.stringify(options)}` : key,
}));

// Radix probes pointer capture on a controlled dialog; jsdom ships neither it
// nor PointerEvent. Same polyfills as `ChatKnowledge.test.tsx`.
beforeAll(() => {
  if (!window.PointerEvent) {
    Object.defineProperty(window, 'PointerEvent', { configurable: true, value: MouseEvent });
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

const spaceA: KnowledgeSpace = { id: '7', name: 'space-a' } as unknown as KnowledgeSpace;
const spaceB: KnowledgeSpace = { id: '8', name: 'space-b' } as unknown as KnowledgeSpace;
const folder = { id: '20', name: 'reports', type: FileType.FOLDER } as unknown as KnowledgeFile;
const fileA = { id: '21', name: 'a.pdf', type: FileType.PDF } as unknown as KnowledgeFile;
const fileB = { id: '22', name: 'b.pdf', type: FileType.PDF } as unknown as KnowledgeFile;
const fileTxt = { id: '23', name: 'notes.txt', type: FileType.TXT } as unknown as KnowledgeFile;
// Two more files, reachable only through the folder — they make "this location
// only" observable: the ids never collide with the root's.
const fileC = { id: '24', name: 'c.pdf', type: FileType.PDF } as unknown as KnowledgeFile;
const fileD = { id: '25', name: 'd.pdf', type: FileType.PDF } as unknown as KnowledgeFile;

const pages = (rows: KnowledgeFile[]) => ({ data: { pages: [{ data: rows }] } });

const renderPicker = (props: Partial<ComponentProps<typeof KnowledgeFilePicker>> = {}) => {
  const onConfirm = jest.fn();
  const onClose = jest.fn();
  render(<KnowledgeFilePicker open multiple={false} onConfirm={onConfirm} onClose={onClose} {...props} />);
  return { onConfirm, onClose };
};

/** The arguments the picker last passed to the children hook. */
const lastChildrenCall = () => mockUseSpaceChildren.mock.calls.at(-1) as unknown[];

const isConfirmDisabled = () => (screen.getByTestId('picker-confirm') as HTMLButtonElement).disabled;

beforeEach(() => {
  mockUseCategorizedSpaces.mockReturnValue({
    groups: [{ key: 'created', label: 'created-by-me', spaces: [spaceA, spaceB] }],
    isFetching: false,
  });
  // Location-aware: the folder holds a different (disjoint) set of files, so a
  // test can tell "the current folder" apart from "everything picked so far".
  mockUseSpaceChildren.mockImplementation((_space: unknown, parentId: string | null) => ({
    ...pages(parentId === '20' ? [fileC, fileD] : [folder, fileA, fileB, fileTxt]),
    isFetching: false,
    isError: false,
    hasNextPage: false,
    fetchNextPage: jest.fn(),
  }));
});

describe('KnowledgeFilePicker — two panes (AC-01 / AC-06)', () => {
  it('keeps the spaces in the left rail and opens the first one', () => {
    renderPicker();
    expect(screen.getByTestId('picker-space-7')).toBeTruthy();
    expect(screen.getByTestId('picker-space-8')).toBeTruthy();
    // Auto-selected, so the right pane is already browsing space 7's root.
    expect(screen.getByTestId('picker-file-21')).toBeTruthy();
    expect((lastChildrenCall()[0] as KnowledgeSpace).id).toBe('7');
  });

  it('switches the right pane without losing the rail', () => {
    renderPicker();
    fireEvent.click(screen.getByTestId('picker-space-8'));

    expect((lastChildrenCall()[0] as KnowledgeSpace).id).toBe('8');
    // The left rail is a persistent list, not a step that gets replaced.
    expect(screen.getByTestId('picker-space-7')).toBeTruthy();
  });

  it('descends into a folder instead of selecting it (AC-06)', () => {
    const { onConfirm } = renderPicker();
    fireEvent.click(screen.getByTestId('picker-folder-20'));

    expect(lastChildrenCall()[1]).toBe('20'); // parentId is now the folder
    // A folder click is not a pick: nothing to confirm.
    expect(isConfirmDisabled()).toBe(true);
    expect(onConfirm).not.toHaveBeenCalled();
  });

  it('walks back up the path', () => {
    renderPicker();
    fireEvent.click(screen.getByTestId('picker-folder-20'));
    fireEvent.click(screen.getByTestId('picker-back'));

    expect(lastChildrenCall()[1]).toBeNull(); // back at the space root
    expect(screen.queryByTestId('picker-back')).toBeNull();
  });

  it('searches within the current location', () => {
    renderPicker();
    fireEvent.change(screen.getByTestId('picker-search'), { target: { value: 'rep' } });

    expect(lastChildrenCall()[2]).toBe('rep');
  });

  it('says which empty state it is in', () => {
    mockUseSpaceChildren.mockReturnValue({
      data: { pages: [{ data: [] }] },
      isFetching: false,
      isError: false,
      hasNextPage: false,
      fetchNextPage: jest.fn(),
    });
    renderPicker();
    expect(screen.getByText('com_knowledge.no_file_here')).toBeTruthy();

    fireEvent.change(screen.getByTestId('picker-search'), { target: { value: 'rep' } });
    expect(screen.getByText('com_knowledge.no_matched_file')).toBeTruthy();
  });
});

describe('KnowledgeFilePicker — the field type gate (AC-29)', () => {
  it('greys out files the field would not accept', () => {
    renderPicker({ suffixes: ['.pdf'], multiple: true });

    const accepted = screen.getByTestId('picker-file-21');
    const rejected = screen.getByTestId('picker-file-23');
    expect(rejected.getAttribute('aria-disabled')).toBe('true');
    expect(rejected.textContent).toContain('com_knowledge.pick_reason_suffix');

    fireEvent.click(rejected);
    expect(rejected.getAttribute('aria-pressed')).toBe('false');
    expect(isConfirmDisabled()).toBe(true);

    fireEvent.click(accepted);
    expect(accepted.getAttribute('aria-pressed')).toBe('true');
    expect(isConfirmDisabled()).toBe(false);
  });

  it('accepts everything when the field declares no type list', () => {
    renderPicker({ multiple: true });
    fireEvent.click(screen.getByTestId('picker-file-21'));
    fireEvent.click(screen.getByTestId('picker-file-22'));

    expect(isConfirmDisabled()).toBe(false);
    expect(screen.getByTestId('picker-selected-count').textContent).toContain('"0":2');
  });
});

describe('KnowledgeFilePicker — select all in this folder (AC-30 / AC-31 / AC-32)', () => {
  it('picks every acceptable file of the location, and nothing else', () => {
    renderPicker({ multiple: true, suffixes: ['.pdf'] });
    fireEvent.click(screen.getByTestId('picker-select-all'));

    // The .txt and the folder are outside "all" — the type gate still applies.
    expect(screen.getByTestId('picker-file-21').getAttribute('aria-pressed')).toBe('true');
    expect(screen.getByTestId('picker-file-22').getAttribute('aria-pressed')).toBe('true');
    expect(screen.getByTestId('picker-file-23').getAttribute('aria-pressed')).toBe('false');
    expect(screen.getByTestId('picker-selected-count').textContent).toContain('"0":2');
  });

  it('shows a partial pick as indeterminate', () => {
    renderPicker({ multiple: true });
    expect(screen.getByTestId('picker-select-all').getAttribute('data-state')).toBe('unchecked');

    fireEvent.click(screen.getByTestId('picker-file-21'));
    expect(screen.getByTestId('picker-select-all').getAttribute('data-state')).toBe('indeterminate');

    fireEvent.click(screen.getByTestId('picker-select-all'));
    expect(screen.getByTestId('picker-select-all').getAttribute('data-state')).toBe('checked');
  });

  it('un-picks only the current folder, leaving other folders alone', () => {
    renderPicker({ multiple: true });
    fireEvent.click(screen.getByTestId('picker-select-all')); // root: 21, 22, 23
    expect(screen.getByTestId('picker-selected-count').textContent).toContain('"0":3');

    fireEvent.click(screen.getByTestId('picker-folder-20'));
    fireEvent.click(screen.getByTestId('picker-select-all')); // folder: + 24, 25
    expect(screen.getByTestId('picker-selected-count').textContent).toContain('"0":5');

    fireEvent.click(screen.getByTestId('picker-select-all')); // un-pick the folder's two
    expect(screen.getByTestId('picker-selected-count').textContent).toContain('"0":3');
  });

  it('clears picks from every folder at once', () => {
    renderPicker({ multiple: true });
    fireEvent.click(screen.getByTestId('picker-select-all'));
    fireEvent.click(screen.getByTestId('picker-folder-20'));
    fireEvent.click(screen.getByTestId('picker-clear-all'));

    expect(screen.getByTestId('picker-selected-count').textContent).toContain('"0":0');
    expect(isConfirmDisabled()).toBe(true);
  });

  it('disables the row when the field accepts nothing here', () => {
    renderPicker({ multiple: true, suffixes: ['.docx'] });
    expect((screen.getByTestId('picker-select-all') as HTMLButtonElement).disabled).toBe(true);
  });

  it('offers neither entry in single-select (AC-32)', () => {
    renderPicker({ multiple: false });
    expect(screen.queryByTestId('picker-select-all')).toBeNull();
    expect(screen.queryByTestId('picker-clear-all')).toBeNull();
  });
});

describe('KnowledgeFilePicker — single select (AC-03)', () => {
  it('keeps only the newest pick', () => {
    const { onConfirm } = renderPicker({ multiple: false });
    fireEvent.click(screen.getByTestId('picker-file-21'));
    fireEvent.click(screen.getByTestId('picker-file-22'));
    fireEvent.click(screen.getByTestId('picker-confirm'));

    expect(onConfirm).toHaveBeenCalledTimes(1);
    expect(onConfirm.mock.calls[0][0]).toEqual([{ spaceId: '7', fileId: '22', name: 'b.pdf' }]);
  });

  it('un-picks a file when it is clicked again', () => {
    const { onConfirm } = renderPicker({ multiple: false });
    fireEvent.click(screen.getByTestId('picker-file-21'));
    fireEvent.click(screen.getByTestId('picker-file-21'));

    expect(isConfirmDisabled()).toBe(true);
    expect(onConfirm).not.toHaveBeenCalled();
  });
});

describe('KnowledgeFilePicker — multi select (AC-02)', () => {
  it('hands back every picked file', () => {
    const { onConfirm } = renderPicker({ multiple: true });
    fireEvent.click(screen.getByTestId('picker-file-21'));
    fireEvent.click(screen.getByTestId('picker-file-22'));
    fireEvent.click(screen.getByTestId('picker-confirm'));

    expect(onConfirm).toHaveBeenCalledWith([
      { spaceId: '7', fileId: '21', name: 'a.pdf' },
      { spaceId: '7', fileId: '22', name: 'b.pdf' },
    ]);
  });
});

describe('KnowledgeFilePicker — dismissal (AC-05)', () => {
  it('cancelling never confirms, even with picks in hand', () => {
    const { onConfirm, onClose } = renderPicker();
    fireEvent.click(screen.getByTestId('picker-file-21'));
    fireEvent.click(screen.getByTestId('picker-cancel'));

    expect(onConfirm).not.toHaveBeenCalled();
    expect(onClose).toHaveBeenCalled();
  });

  it('confirming an empty selection confirms nothing', () => {
    const { onConfirm, onClose } = renderPicker();
    fireEvent.click(screen.getByTestId('picker-confirm'));

    expect(onConfirm).not.toHaveBeenCalled();
    expect(onClose).not.toHaveBeenCalled();
  });
});
