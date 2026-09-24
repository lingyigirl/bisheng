/**
 * F065 T-05 / T-13 — which spaces the user can see (AC-06) and the merge rule
 * that keeps them de-duplicated.
 */

import type { KnowledgeSpace } from '~/api/knowledge';
import { fetchVisibleSpaces, mergeVisibleSpaces } from './visibleSpaces';

const mockMine = jest.fn();
const mockJoined = jest.fn();
const mockDepartment = jest.fn();

jest.mock('~/api/knowledge', () => ({
  getMineSpacesApi: (...args: unknown[]) => mockMine(...args),
  getJoinedSpacesApi: (...args: unknown[]) => mockJoined(...args),
  getDepartmentSpacesApi: (...args: unknown[]) => mockDepartment(...args),
}));

const space = (id: string | number, name: string): KnowledgeSpace =>
  ({ id, name }) as unknown as KnowledgeSpace;

describe('mergeVisibleSpaces', () => {
  it('merges the sources in order', () => {
    const merged = mergeVisibleSpaces([space('1', 'mine')], [space('2', 'joined')], [space('3', 'dept')]);
    expect(merged.map((s) => s.name)).toEqual(['mine', 'joined', 'dept']);
  });

  it('de-duplicates by id, keeping the first occurrence', () => {
    // A space you created AND are joined to comes back twice — one row only.
    const merged = mergeVisibleSpaces(
      [space('1', 'mine')],
      [space('1', 'mine-again'), space('2', 'joined')],
      null,
      undefined,
    );
    expect(merged.map((s) => s.name)).toEqual(['mine', 'joined']);
  });

  it('drops entries without a usable id', () => {
    const merged = mergeVisibleSpaces([space('', 'no-id'), {} as KnowledgeSpace, space('9', 'ok')]);
    expect(merged.map((s) => s.name)).toEqual(['ok']);
  });

  it('tolerates missing lists', () => {
    expect(mergeVisibleSpaces()).toEqual([]);
    expect(mergeVisibleSpaces(null, undefined)).toEqual([]);
  });
});

describe('fetchVisibleSpaces', () => {
  it('reads the three sources and merges them', async () => {
    mockMine.mockResolvedValue([space('1', 'mine')]);
    mockJoined.mockResolvedValue([space('1', 'mine'), space('2', 'joined')]);
    mockDepartment.mockResolvedValue([space('3', 'dept')]);

    const spaces = await fetchVisibleSpaces();

    expect(spaces.map((s) => s.name)).toEqual(['mine', 'joined', 'dept']);
    expect(mockMine).toHaveBeenCalledTimes(1);
    expect(mockJoined).toHaveBeenCalledTimes(1);
    expect(mockDepartment).toHaveBeenCalledTimes(1);
  });
});
