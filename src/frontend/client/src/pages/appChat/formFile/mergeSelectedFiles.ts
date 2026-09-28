import type { FormFileEntry } from './types';

/**
 * Merge newly selected files into a field's current list (AC-09 ~ AC-12).
 *
 * Multi-select appends and drops name collisions. The collision check is by
 * NAME and spans both sources, so "picked from the knowledge space once, then
 * uploaded the same document" lands in the list once (AC-10). Single-select
 * keeps only the newest file — the existing overwrite behaviour, expressed as a
 * list of length 1 (AC-11).
 *
 * When nothing is added the current array is returned by reference: the caller
 * uses that identity to skip a pointless state update.
 */
export function mergeSelectedFiles(
  existing: readonly FormFileEntry[] | null | undefined,
  incoming: readonly FormFileEntry[] | null | undefined,
  multiple: boolean,
): FormFileEntry[] {
  const current = Array.isArray(existing) ? existing : [];
  const next = Array.isArray(incoming) ? incoming : [];
  if (next.length === 0) return current as FormFileEntry[];

  if (!multiple) return [next[next.length - 1]];

  // Seed from the existing list, then keep filling it as we go — that also drops
  // duplicates *within* the incoming batch.
  const seenNames = new Set(current.map((file) => file.name));
  const added: FormFileEntry[] = [];
  for (const file of next) {
    if (seenNames.has(file.name)) continue;
    seenNames.add(file.name);
    added.push(file);
  }

  return added.length === 0 ? (current as FormFileEntry[]) : [...current, ...added];
}
