/**
 * F065 T-08 — picked files → field values (AC-04 / AC-07).
 */

import { resolvePickedValues } from './resolvePickedValues';
import type { PickedKnowledgeFile } from './resolvePickedValues';

const mockGetFileDownloadApi = jest.fn();
jest.mock('~/api/knowledge', () => ({
  getFileDownloadApi: (...args: unknown[]) => mockGetFileDownloadApi(...args),
}));

const picked = (fileId: string, name = `${fileId}.pdf`): PickedKnowledgeFile => ({
  spaceId: '7',
  fileId,
  name,
});

describe('resolvePickedValues — AC-04', () => {
  it('turns every pick into a knowledge-sourced entry carrying its download address', async () => {
    mockGetFileDownloadApi.mockResolvedValue({ original_url: 'https://minio/a?X-Amz-Algorithm=x' });

    const { entries, failed } = await resolvePickedValues([picked('1'), picked('2')]);

    expect(failed).toEqual([]);
    expect(entries).toEqual([
      { name: '1.pdf', value: 'https://minio/a?X-Amz-Algorithm=x', source: 'knowledge' },
      { name: '2.pdf', value: 'https://minio/a?X-Amz-Algorithm=x', source: 'knowledge' },
    ]);
    expect(mockGetFileDownloadApi).toHaveBeenCalledWith('7', '1');
  });

  it('trims the address so a stray newline cannot leak into the submitted value', async () => {
    mockGetFileDownloadApi.mockResolvedValue({ original_url: '  https://minio/a  ' });
    const { entries } = await resolvePickedValues([picked('1')]);
    expect(entries[0].value).toBe('https://minio/a');
  });
});

describe('resolvePickedValues — AC-07', () => {
  it('drops only the file that failed and reports its name', async () => {
    mockGetFileDownloadApi.mockImplementation((_spaceId: string, fileId: string) =>
      fileId === 'bad'
        ? Promise.reject(new Error('no download permission'))
        : Promise.resolve({ original_url: 'https://minio/ok' }),
    );

    const { entries, failed } = await resolvePickedValues([picked('ok', 'ok.pdf'), picked('bad', 'bad.pdf')]);

    expect(entries.map((e) => e.name)).toEqual(['ok.pdf']);
    expect(failed).toEqual(['bad.pdf']);
  });

  it('treats an empty address as a failure rather than submitting a blank', async () => {
    mockGetFileDownloadApi.mockResolvedValue({ original_url: '' });
    const { entries, failed } = await resolvePickedValues([picked('1')]);
    expect(entries).toEqual([]);
    expect(failed).toEqual(['1.pdf']);
  });

  it('reports every failure when nothing resolves', async () => {
    mockGetFileDownloadApi.mockRejectedValue(new Error('boom'));
    const { entries, failed } = await resolvePickedValues([picked('1'), picked('2')]);
    expect(entries).toEqual([]);
    expect(failed).toEqual(['1.pdf', '2.pdf']);
  });

  it('does nothing for an empty selection', async () => {
    const { entries, failed } = await resolvePickedValues([]);
    expect(entries).toEqual([]);
    expect(failed).toEqual([]);
    expect(mockGetFileDownloadApi).not.toHaveBeenCalled();
    expect((await resolvePickedValues(null)).entries).toEqual([]);
  });
});
