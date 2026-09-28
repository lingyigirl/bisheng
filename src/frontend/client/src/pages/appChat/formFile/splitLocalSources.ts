import type { FormFileEntry } from './types';

/**
 * The local uploads among a field's selected files — the only ones the archive
 * step acts on (AC-21).
 *
 * Knowledge-space picks are referenced by address and are deliberately NOT
 * copied into the archive space (spec 范围边界): copying them would store the
 * same content twice in the same space, which is exactly what the duplicate
 * check exists to prevent.
 */
export function splitLocalSources(
  entries: readonly FormFileEntry[] | null | undefined,
): FormFileEntry[] {
  return (Array.isArray(entries) ? entries : []).filter((entry) => entry.source === 'local');
}
