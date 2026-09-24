/**
 * F065 T-05 — data layer for the knowledge-space file browser.
 *
 * react-query only: Recoil is frozen (client `AGENTS.md`), so nothing here may
 * add an atom. The pure helpers below are exported so the filtering and paging
 * rules can be unit-tested without rendering the picker.
 *
 * The space list is NOT served from here: the picker renders it grouped
 * (部门 / 我创建的 / 我加入的) via `~/hooks/useCategorizedKnowledgeSpaces`, which
 * reads the same three endpoints `../../formFile/visibleSpaces` merges for the
 * archive step (design §4.1) — one answer, two consumers.
 */

import { useInfiniteQuery } from '@tanstack/react-query';
import {
  FileType,
  SPACE_CHILDREN_STATUS_NUMS_EXCLUDE_FAILED,
  SpaceRole,
  getSpaceChildrenApi,
  searchSpaceChildrenApi,
  type KnowledgeFile,
  type KnowledgeSpace,
} from '~/api/knowledge';

/** One page of children; large enough that most spaces need a single request. */
const PAGE_SIZE = 200;

export const knowledgePickerKeys = {
  children: (spaceId: string, parentId: string | null, keyword: string) =>
    ['f065-knowledge-picker', 'children', spaceId, parentId ?? 'root', keyword || 'browse'] as const,
};

/** One row in the browser: a folder to descend into, or a file to pick. */
export interface BrowseChild {
  id: string;
  name: string;
  isFolder: boolean;
  size?: number;
}

/** What both children endpoints answer with; the browse one adds a cursor. */
export interface ChildrenPage {
  data: KnowledgeFile[];
  has_more: boolean;
  next_cursor?: string | null;
}

/**
 * The child-status filter the rest of the knowledge UI uses: members must not
 * see generic-failed rows, while admins/creators do. Mirrors `listKnowledgeFolders`.
 */
export function childStatusFilter(role: SpaceRole | undefined): number[] | undefined {
  return role === SpaceRole.MEMBER ? SPACE_CHILDREN_STATUS_NUMS_EXCLUDE_FAILED : undefined;
}

/** Split a children page into browsable rows, keeping the order the server sent. */
export function toBrowseChildren(files: readonly KnowledgeFile[] | null | undefined): BrowseChild[] {
  return (Array.isArray(files) ? files : [])
    .filter((file) => !!file?.id)
    .map((file) => ({
      id: String(file.id),
      name: file.name ?? '',
      isFolder: file.type === FileType.FOLDER,
      size: file.size,
    }));
}

/**
 * How the NEXT page is addressed — and the reason the two modes cannot share one
 * paging rule: browsing walks a **cursor**, searching takes a **page number**,
 * and the search endpoint answers `has_more` without a cursor (F040). Returning
 * `undefined` ends the run in both modes.
 */
export function nextPageParamFor(keyword: string) {
  return (last: ChildrenPage, pages: ChildrenPage[]) =>
    keyword
      ? last.has_more
        ? pages.length + 1
        : undefined
      : last.has_more
        ? last.next_cursor ?? undefined
        : undefined;
}

/**
 * One page of the current location (a space root or a folder), optionally
 * narrowed by a keyword.
 *
 * Folders and files come back in the same response and are split by `type`, so
 * one request drives the whole list — no separate folder call to keep in sync.
 * A keyword searches **within the current folder**, exactly as browsing does.
 */
export function useSpaceChildren(
  space: KnowledgeSpace | undefined,
  parentId: string | null,
  keyword: string,
  enabled: boolean,
) {
  const spaceId = space?.id != null ? String(space.id) : '';
  const statusFilter = childStatusFilter(space?.role);

  return useInfiniteQuery<ChildrenPage>(
    [...knowledgePickerKeys.children(spaceId, parentId, keyword), statusFilter?.join(',') ?? 'all'],
    ({ pageParam }) =>
      keyword
        ? searchSpaceChildrenApi({
            space_id: spaceId,
            parent_id: parentId ?? undefined,
            keyword,
            // Search pages are numbers; the cursor branch below never runs here.
            page: typeof pageParam === 'number' ? pageParam : 1,
            page_size: PAGE_SIZE,
            file_status: statusFilter,
          })
        : getSpaceChildrenApi({
            space_id: spaceId,
            parent_id: parentId ?? undefined,
            cursor: typeof pageParam === 'string' ? pageParam : undefined,
            page_size: PAGE_SIZE,
            file_status: statusFilter,
          }),
    {
      getNextPageParam: nextPageParamFor(keyword),
      enabled: enabled && !!spaceId,
      refetchOnWindowFocus: false,
      refetchOnReconnect: false,
    },
  );
}
