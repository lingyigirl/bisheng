/**
 * F065 T-08 — picked knowledge-space files → form field values (AC-04 / AC-07).
 */

import { getFileDownloadApi } from '~/api/knowledge';
import type { FormFileEntry } from '../../formFile/types';

/** A file chosen in the knowledge-space browser — enough to ask for its address. */
export interface PickedKnowledgeFile {
  /** Knowledge-space the file lives in. */
  spaceId: string;
  /** Knowledge file id. */
  fileId: string;
  /** Display name; also the de-duplication key used by `mergeSelectedFiles`. */
  name: string;
}

export interface ResolvedPickedFiles {
  entries: FormFileEntry[];
  /** Names whose address could not be obtained — the caller surfaces these (AC-07). */
  failed: string[];
}

/**
 * Resolve each picked file to its download address.
 *
 * The address is the only thing the workflow input node understands: it fetches
 * the address and parses the content, exactly as it does for a locally uploaded
 * file (design 决策 3 / 坑 6). One file failing to resolve must not sink the
 * whole selection — it is dropped and reported, the rest are returned (AC-07).
 */
export async function resolvePickedValues(
  picked: readonly PickedKnowledgeFile[] | null | undefined,
): Promise<ResolvedPickedFiles> {
  const list = Array.isArray(picked) ? picked : [];

  const settled = await Promise.all(
    list.map(async (file) => {
      try {
        const { original_url: originalUrl } = await getFileDownloadApi(file.spaceId, file.fileId);
        return { file, url: typeof originalUrl === 'string' ? originalUrl.trim() : '' };
      } catch {
        return { file, url: '' };
      }
    }),
  );

  const entries: FormFileEntry[] = [];
  const failed: string[] = [];
  for (const { file, url } of settled) {
    if (!url) {
      failed.push(file.name);
      continue;
    }
    entries.push({ name: file.name, value: url, source: 'knowledge' });
  }

  return { entries, failed };
}
