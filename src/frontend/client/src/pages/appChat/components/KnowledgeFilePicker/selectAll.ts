/**
 * F065 — "select every file in this folder" (AC-30 / AC-31).
 *
 * The scope is the CURRENT location and only the rows the field would accept:
 * folders are never pickable, and a file the field's own type list refuses
 * cannot be mass-selected into a value it would then reject (AC-29). Picks made
 * in other folders stay untouched — the caller merges, it does not replace.
 *
 * Pure so the rules can be exercised without rendering the dialog; the picker
 * itself only maps this to its `spaceId:fileId` selection keys.
 */

import type { BrowseChild } from './useKnowledgeBrowse';
import { matchesSuffix } from './suffixMatch';

/** Ids of the rows a "select all" in this location may pick, in list order. */
export function selectableFileIds(
  children: readonly BrowseChild[] | null | undefined,
  suffixes: readonly string[] | undefined,
): string[] {
  return (Array.isArray(children) ? children : [])
    .filter((child) => !child.isFolder && matchesSuffix(child.name, suffixes))
    .map((child) => child.id);
}

/** What the select-all checkbox shows for a location. */
export type TriState = boolean | 'indeterminate';

/**
 * All picked → checked; some picked → indeterminate; none → unchecked.
 *
 * `total === 0` (nothing selectable here) is unchecked rather than a vacuous
 * "all selected" — the row is disabled in that case anyway.
 */
export function triState(pickedCount: number, total: number): TriState {
  if (total > 0 && pickedCount >= total) return true;
  return pickedCount > 0 ? 'indeterminate' : false;
}
