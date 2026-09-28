/**
 * F065 T-05 — browse helpers (AC-01 / AC-06).
 *
 * The hooks are thin react-query wrappers; what carries rules here is the
 * status filter, the folder/file split and the paging rule. "Which spaces" is
 * not here at all — the picker groups them via
 * `~/hooks/useCategorizedKnowledgeSpaces`, the archive step merges them via
 * `../../formFile/visibleSpaces.test.ts`.
 */

import {
  SPACE_CHILDREN_STATUS_NUMS_EXCLUDE_FAILED,
  SpaceRole,
  FileType,
  type KnowledgeFile,
} from '~/api/knowledge';
import {
  childStatusFilter,
  nextPageParamFor,
  toBrowseChildren,
  type ChildrenPage,
} from './useKnowledgeBrowse';

const file = (id: string, name: string, type: FileType): KnowledgeFile =>
  ({ id, name, type }) as unknown as KnowledgeFile;

describe('childStatusFilter — mirrors the other knowledge read surfaces', () => {
  it('hides generic-failed rows from members', () => {
    expect(childStatusFilter(SpaceRole.MEMBER)).toEqual(SPACE_CHILDREN_STATUS_NUMS_EXCLUDE_FAILED);
  });

  it('leaves admins / creators unfiltered', () => {
    expect(childStatusFilter(SpaceRole.ADMIN)).toBeUndefined();
    expect(childStatusFilter(SpaceRole.CREATOR)).toBeUndefined();
    expect(childStatusFilter(undefined)).toBeUndefined();
  });
});

describe('toBrowseChildren — AC-01 / AC-06', () => {
  it('tells folders and files apart', () => {
    const rows = toBrowseChildren([
      file('10', 'reports', FileType.FOLDER),
      file('11', 'a.pdf', FileType.PDF),
    ]);
    expect(rows).toEqual([
      { id: '10', name: 'reports', isFolder: true, size: undefined },
      { id: '11', name: 'a.pdf', isFolder: false, size: undefined },
    ]);
  });

  it('keeps the listed order and drops entries without an id', () => {
    const rows = toBrowseChildren([file('', 'gone', FileType.PDF), file('1', 'a.pdf', FileType.PDF)]);
    expect(rows.map((r) => r.id)).toEqual(['1']);
  });

  it('tolerates a missing page', () => {
    expect(toBrowseChildren(null)).toEqual([]);
    expect(toBrowseChildren(undefined)).toEqual([]);
  });
});

describe('nextPageParamFor — one hook, two paging rules (AC-01)', () => {
  it('walks the cursor while browsing', () => {
    const next = nextPageParamFor('');
    expect(next({ data: [], has_more: true, next_cursor: 'c1' }, [])).toBe('c1');
    expect(next({ data: [], has_more: false, next_cursor: 'c1' }, [])).toBeUndefined();
  });

  it('ends the run when a page is open but carries no cursor', () => {
    const next = nextPageParamFor('');
    expect(next({ data: [], has_more: true, next_cursor: null }, [])).toBeUndefined();
  });

  it('counts pages while searching — that endpoint has no cursor (F040)', () => {
    const loaded: ChildrenPage[] = [
      { data: [], has_more: true },
      { data: [], has_more: true },
    ];
    const next = nextPageParamFor('report');
    expect(next(loaded[1], loaded)).toBe(3);
    expect(next({ data: [], has_more: false }, loaded)).toBeUndefined();
  });
});
