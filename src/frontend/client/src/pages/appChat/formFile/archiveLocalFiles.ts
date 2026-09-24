/**
 * F065 Wave D — archive a form's local uploads into the user's 「应用文件」 space.
 *
 * Everything here runs AFTER the form has been submitted and must never block
 * it: the caller catches and logs (AC-22, design 决策 7).
 */

import {
  FileType,
  addFilesApi,
  createFolderApi,
  createSpaceApi,
  getSpaceChildrenApi,
  uploadFileToServerApi,
  type KnowledgeSpace,
} from '~/api/knowledge';
import { resolveFolderName } from './resolveFolderName';
import type { FormFieldLike, FormFileEntry } from './types';
import { fetchVisibleSpaces } from './visibleSpaces';

/**
 * The well-known archive space (AC-14).
 *
 * A cross-feature data convention, NOT UI copy: the space is looked up by this
 * exact name, so it must never be translated (release-contract INV-22).
 */
// eslint-disable-next-line no-restricted-syntax -- cross-feature data convention, not UI copy
export const ARCHIVE_SPACE_NAME = '应用文件';

/** What the archive step did; surfaced for logging only. */
export interface ArchiveOutcome {
  /** Files registered into the archive space. */
  added: number;
  /** Files already present by content, skipped (AC-20). */
  skippedDuplicates: number;
}

/**
 * Resolve the archive space, creating it when the user has none (AC-19/AC-26).
 *
 * Created PRIVATE (AC-27): the space is the user's own, so the look-up by name
 * finds their space and their uploads stay invisible to everyone else
 * (design 决策 6).
 */
export async function resolveArchiveSpace(): Promise<KnowledgeSpace> {
  const spaces = await fetchVisibleSpaces();
  const existing = spaces.find((space) => space.name === ARCHIVE_SPACE_NAME);
  if (existing) return existing;
  return await createSpaceApi({ name: ARCHIVE_SPACE_NAME, auth_type: 'private' });
}

/**
 * Resolve the target folder in the space root, creating it when absent
 * (AC-17 / AC-18). Existing folders are reused — never duplicated.
 */
export async function resolveArchiveFolder(spaceId: string, folderName: string): Promise<string> {
  const page = await getSpaceChildrenApi({ space_id: spaceId, page_size: 200 });
  const existing = page.data.find(
    (child) => child.type === FileType.FOLDER && child.name === folderName,
  );
  if (existing) return String(existing.id);

  const created = await createFolderApi(spaceId, { name: folderName, parent_id: null });
  return String(created.id);
}

/**
 * Register one local file into the folder, skipping duplicates ALREADY IN THAT
 * FOLDER (AC-14 / AC-20).
 *
 * The duplicate PRE-check is not an optimisation — it is the whole point.
 * `addFilesApi` answers a duplicate by MUTATING the existing row: it repoints
 * `object_name` at a tmp object holding the new content, writes a remark and
 * sets the status to FAILED, waiting for the client to call the retry endpoint
 * to overwrite. Calling it and then ignoring the `status === 3` row would leave
 * a previously-good document flagged as failed, which AC-20 forbids (design
 * 决策 5 / 坑 1).
 *
 * Both calls carry `dedup_scope: 'folder'` with the target folder, so the
 * question asked is "is this already in THIS folder?" — NOT "anywhere in the
 * space". The same material filed under two customer folders must land in both
 * (design 决策 11). The two calls MUST agree on the scope: a space-wide
 * pre-check would skip files the folder-scoped registration would have added.
 *
 * The upload endpoint's `repeat` flag runs the same duplicate query the
 * registration would, against the same scope — so skipping on it is equivalent,
 * and side-effect free.
 *
 * @returns true when the file was registered, false when it was a duplicate here.
 */
export async function addLocalFileToArchive(
  spaceId: string,
  folderId: string,
  file: File,
): Promise<boolean> {
  const parentId = Number(folderId);
  const uploaded = await uploadFileToServerApi(spaceId, file, undefined, {
    parentId,
    dedupScope: 'folder',
  });
  if (uploaded?.repeat) return false;
  await addFilesApi(spaceId, {
    file_path: [uploaded.file_path],
    parent_id: parentId,
    dedup_scope: 'folder',
  });
  return true;
}

export interface ArchiveLocalFilesInput {
  /** Local entries across every file field of the submitting form (AC-21). */
  files: readonly FormFileEntry[];
  /** The form's field definitions — used to locate the customer-name field (AC-15). */
  fields: readonly FormFieldLike[];
  /** The submitted form values, keyed by field key. */
  values: Record<string, unknown>;
  /** Application name, the folder-name fallback (AC-16). */
  appName: string | null | undefined;
}

/**
 * Archive every local upload of a submitting form (AC-14 ~ AC-21, AC-25).
 *
 * Resolves the space and folder ONCE and shares them: the folder name comes from
 * the customer name rather than from the field, so several file fields of one
 * form belong in the same folder (AC-25).
 *
 * Files are registered SEQUENTIALLY on purpose. Two byte-identical files picked
 * in different fields would, in parallel, both read "no duplicate yet" and get
 * registered — sequential makes the second one see the first and skip (AC-20).
 */
export async function archiveLocalFiles(input: ArchiveLocalFilesInput): Promise<ArchiveOutcome> {
  const outcome: ArchiveOutcome = { added: 0, skippedDuplicates: 0 };

  const files = input.files.filter((entry) => entry.source === 'local' && !!entry.file);
  if (!files.length) return outcome;

  const folderName = resolveFolderName(input.fields, input.values, input.appName);
  // Nothing to name the folder after — dirty data rather than a user choice, so
  // the archive is skipped instead of creating a nameless folder (spec §3).
  if (!folderName) return outcome;

  const space = await resolveArchiveSpace();
  const spaceId = String(space.id);
  // Resolve/create the folder before the loop: it is also the pre-check's parent,
  // and doing it once is what keeps concurrent fields out of a create race.
  const folderId = await resolveArchiveFolder(spaceId, folderName);

  for (const entry of files) {
    const added = await addLocalFileToArchive(spaceId, folderId, entry.file as File);
    if (added) outcome.added += 1;
    else outcome.skippedDuplicates += 1;
  }

  return outcome;
}
