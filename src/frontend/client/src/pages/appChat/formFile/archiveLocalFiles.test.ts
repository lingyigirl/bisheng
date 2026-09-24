/**
 * F065 Wave D — archiving local uploads (AC-14 ~ AC-21, AC-25, AC-26, AC-27).
 *
 * The load-bearing case is "a duplicate is never handed to `addFilesApi`":
 * that endpoint answers a duplicate by mutating the existing row into FAILED
 * (design 坑 1), so the pre-check on the upload response's `repeat` flag is what
 * keeps AC-20 from corrupting a previously-good document.
 */

import type { KnowledgeSpace } from '~/api/knowledge';
import {
  ARCHIVE_SPACE_NAME,
  addLocalFileToArchive,
  archiveLocalFiles,
  resolveArchiveFolder,
  resolveArchiveSpace,
} from './archiveLocalFiles';
import { CUSTOMER_NAME_LABEL } from './resolveFolderName';
import type { FormFileEntry } from './types';

const mockMine = jest.fn();
const mockJoined = jest.fn();
const mockDept = jest.fn();
const mockCreateSpace = jest.fn();
const mockChildren = jest.fn();
const mockCreateFolder = jest.fn();
const mockUpload = jest.fn();
const mockAddFiles = jest.fn();

jest.mock('~/api/knowledge', () => ({
  FileType: { FOLDER: 'folder', PDF: 'pdf' },
  getMineSpacesApi: (...args: unknown[]) => mockMine(...args),
  getJoinedSpacesApi: (...args: unknown[]) => mockJoined(...args),
  getDepartmentSpacesApi: (...args: unknown[]) => mockDept(...args),
  createSpaceApi: (...args: unknown[]) => mockCreateSpace(...args),
  getSpaceChildrenApi: (...args: unknown[]) => mockChildren(...args),
  createFolderApi: (...args: unknown[]) => mockCreateFolder(...args),
  uploadFileToServerApi: (...args: unknown[]) => mockUpload(...args),
  addFilesApi: (...args: unknown[]) => mockAddFiles(...args),
}));

const space = (id: string, name: string): KnowledgeSpace => ({ id, name }) as unknown as KnowledgeSpace;

const localEntry = (name: string, value = `local://${name}`): FormFileEntry => ({
  name,
  value,
  source: 'local',
  file: new File(['x'], name),
});
const knowledgeEntry = (name: string): FormFileEntry => ({
  name,
  value: `https://minio/${name}`,
  source: 'knowledge',
});

/** The form fields: a customer-name field plus an unrelated one. */
const fields = [{ key: 'cust', value: CUSTOMER_NAME_LABEL }, { key: 'note', value: 'note' }];

beforeEach(() => {
  mockMine.mockResolvedValue([]);
  mockJoined.mockResolvedValue([]);
  mockDept.mockResolvedValue([]);
  mockChildren.mockResolvedValue({ data: [], page_size: 200, has_more: false, next_cursor: null });
  mockUpload.mockResolvedValue({ file_path: 'minio://uploaded', repeat: false });
  mockAddFiles.mockResolvedValue([]);
});

describe('resolveArchiveSpace — AC-19 / AC-26 / AC-27', () => {
  it('reuses the visible space that already has the name', async () => {
    mockMine.mockResolvedValue([space('7', ARCHIVE_SPACE_NAME)]);
    await expect(resolveArchiveSpace()).resolves.toMatchObject({ id: '7' });
    expect(mockCreateSpace).not.toHaveBeenCalled();
  });

  it('creates a PRIVATE space when the user has none (AC-27)', async () => {
    mockCreateSpace.mockResolvedValue(space('42', ARCHIVE_SPACE_NAME));
    await expect(resolveArchiveSpace()).resolves.toMatchObject({ id: '42' });
    expect(mockCreateSpace).toHaveBeenCalledWith({ name: ARCHIVE_SPACE_NAME, auth_type: 'private' });
  });

  it('finds a joined space too, not only a personal one', async () => {
    mockJoined.mockResolvedValue([space('9', ARCHIVE_SPACE_NAME)]);
    await expect(resolveArchiveSpace()).resolves.toMatchObject({ id: '9' });
    expect(mockCreateSpace).not.toHaveBeenCalled();
  });
});

describe('resolveArchiveFolder — AC-17 / AC-18', () => {
  it('reuses an existing folder instead of creating a second one', async () => {
    mockChildren.mockResolvedValue({
      data: [
        { id: '3', name: 'acme', type: 'folder' },
        { id: '4', name: 'acme', type: 'pdf' }, // a FILE with the same name is not the folder
      ],
      page_size: 200,
      has_more: false,
      next_cursor: null,
    });
    await expect(resolveArchiveFolder('7', 'acme')).resolves.toBe('3');
    expect(mockCreateFolder).not.toHaveBeenCalled();
  });

  it('creates the folder at the space root when absent', async () => {
    mockCreateFolder.mockResolvedValue({ id: '11', name: 'acme' });
    await expect(resolveArchiveFolder('7', 'acme')).resolves.toBe('11');
    expect(mockCreateFolder).toHaveBeenCalledWith('7', { name: 'acme', parent_id: null });
  });
});

describe('addLocalFileToArchive — AC-14 / AC-20', () => {
  it('registers a fresh file into the folder', async () => {
    const file = new File(['x'], 'a.pdf');
    await expect(addLocalFileToArchive('7', '3', file)).resolves.toBe(true);
    expect(mockUpload).toHaveBeenCalledWith('7', file, undefined, { parentId: 3, dedupScope: 'folder' });
    expect(mockAddFiles).toHaveBeenCalledWith('7', {
      file_path: ['minio://uploaded'],
      parent_id: 3,
      dedup_scope: 'folder',
    });
  });

  it('asks about this folder, not the whole space — both calls must agree', async () => {
    // The bug this guards: a space-wide pre-check skipped a file that was
    // already filed under ANOTHER customer folder, leaving this folder empty.
    await addLocalFileToArchive('7', '3', new File(['x'], 'a.pdf'));

    expect(mockUpload.mock.calls[0][3]).toEqual({ parentId: 3, dedupScope: 'folder' });
    expect(mockAddFiles.mock.calls[0][1].dedup_scope).toBe('folder');
  });

  it('NEVER calls addFilesApi for a duplicate — that call would corrupt the existing row', async () => {
    mockUpload.mockResolvedValue({ file_path: 'minio://uploaded', repeat: true });
    await expect(addLocalFileToArchive('7', '3', new File(['x'], 'a.pdf'))).resolves.toBe(false);
    expect(mockAddFiles).not.toHaveBeenCalled();
  });
});

describe('archiveLocalFiles — orchestration (AC-15 / AC-16 / AC-21 / AC-25)', () => {
  const input = (files: FormFileEntry[], values: Record<string, unknown>, appName: string) => ({
    files,
    fields,
    values,
    appName,
  });

  it('names the folder after the customer name (AC-15)', async () => {
    mockMine.mockResolvedValue([space('7', ARCHIVE_SPACE_NAME)]);
    mockCreateFolder.mockResolvedValue({ id: '3', name: 'acme' });

    const outcome = await archiveLocalFiles(input([localEntry('a.pdf')], { cust: 'acme' }, 'my-app'));

    expect(mockCreateFolder).toHaveBeenCalledWith('7', { name: 'acme', parent_id: null });
    expect(outcome).toEqual({ added: 1, skippedDuplicates: 0 });
  });

  it('falls back to the application name (AC-16)', async () => {
    mockMine.mockResolvedValue([space('7', ARCHIVE_SPACE_NAME)]);
    mockCreateFolder.mockResolvedValue({ id: '3', name: 'my-app' });

    await archiveLocalFiles(input([localEntry('a.pdf')], { cust: '   ' }, 'my-app'));

    expect(mockCreateFolder).toHaveBeenCalledWith('7', { name: 'my-app', parent_id: null });
  });

  it('does nothing at all when every file came from the knowledge space (AC-21)', async () => {
    const outcome = await archiveLocalFiles(input([knowledgeEntry('a.pdf')], { cust: 'acme' }, 'my-app'));

    expect(outcome).toEqual({ added: 0, skippedDuplicates: 0 });
    expect(mockMine).not.toHaveBeenCalled();
    expect(mockUpload).not.toHaveBeenCalled();
    expect(mockAddFiles).not.toHaveBeenCalled();
  });

  it('does nothing when neither the customer name nor the app name can name a folder', async () => {
    const outcome = await archiveLocalFiles(input([localEntry('a.pdf')], { cust: '' }, '   '));

    expect(outcome).toEqual({ added: 0, skippedDuplicates: 0 });
    expect(mockMine).not.toHaveBeenCalled();
  });

  it('resolves the folder once for several files — one create, both registered (AC-25)', async () => {
    mockMine.mockResolvedValue([space('7', ARCHIVE_SPACE_NAME)]);
    mockCreateFolder.mockResolvedValue({ id: '3', name: 'acme' });

    const outcome = await archiveLocalFiles(
      input([localEntry('a.pdf'), localEntry('b.pdf')], { cust: 'acme' }, 'my-app'),
    );

    expect(mockCreateFolder).toHaveBeenCalledTimes(1);
    expect(mockAddFiles).toHaveBeenCalledTimes(2);
    expect(outcome).toEqual({ added: 2, skippedDuplicates: 0 });
  });

  it('skips duplicates and registers the rest, sequentially', async () => {
    mockMine.mockResolvedValue([space('7', ARCHIVE_SPACE_NAME)]);
    mockCreateFolder.mockResolvedValue({ id: '3', name: 'acme' });
    mockUpload
      .mockResolvedValueOnce({ file_path: 'minio://one', repeat: false })
      .mockResolvedValueOnce({ file_path: 'minio://two', repeat: true });

    const outcome = await archiveLocalFiles(
      input([localEntry('a.pdf'), localEntry('a-copy.pdf')], { cust: 'acme' }, 'my-app'),
    );

    expect(mockAddFiles).toHaveBeenCalledTimes(1);
    expect(outcome).toEqual({ added: 1, skippedDuplicates: 1 });
  });
});
