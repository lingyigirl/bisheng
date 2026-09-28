/**
 * F065 — "which spaces can this user see", in one place.
 *
 * Both consumers need the same answer and must not drift apart: the browser
 * lists them (AC-06), and the archive step resolves the 「应用文件」 space by name
 * among them (AC-19/AC-26). react-query is deliberately absent here so the
 * archive path can call it without dragging the picker's hooks along.
 */

import {
  getDepartmentSpacesApi,
  getJoinedSpacesApi,
  getMineSpacesApi,
  type KnowledgeSpace,
} from '~/api/knowledge';

/**
 * Merge space lists by id, preserving argument order and the first occurrence.
 *
 * Not a plain concat: the same space is routinely returned by more than one
 * endpoint (one you created *and* are joined to), and a duplicate row would let
 * the user pick it twice — or make the archive step create a second space.
 */
export function mergeVisibleSpaces(
  ...lists: ReadonlyArray<readonly KnowledgeSpace[] | null | undefined>
): KnowledgeSpace[] {
  const byId = new Map<string, KnowledgeSpace>();
  for (const list of lists) {
    for (const space of list ?? []) {
      const id = space?.id != null ? String(space.id) : '';
      if (!id || byId.has(id)) continue;
      byId.set(id, space);
    }
  }
  return [...byId.values()];
}

/**
 * Personal + joined + department, merged.
 *
 * The same three sources the other knowledge pickers use
 * (`KnowledgeSpaceSelect`, `useCategorizedKnowledgeSpaces`). `managed` is
 * deliberately NOT requested — it is the backend's mine∪joined merge, so asking
 * for it as well would only duplicate rows; `department` is the one that is
 * absent from the other two and must stay.
 */
export async function fetchVisibleSpaces(): Promise<KnowledgeSpace[]> {
  const [mine, joined, department] = await Promise.all([
    getMineSpacesApi(),
    getJoinedSpacesApi(),
    getDepartmentSpacesApi(),
  ]);
  return mergeVisibleSpaces(mine, joined, department);
}
