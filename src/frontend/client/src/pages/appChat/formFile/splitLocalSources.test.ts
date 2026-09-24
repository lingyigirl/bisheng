/**
 * F065 T-03 — only local uploads are archived (AC-21).
 */

import { splitLocalSources } from './splitLocalSources';
import type { FormFileEntry } from './types';

const local = (name: string): FormFileEntry => ({
  name,
  value: `local://${name}`,
  source: 'local',
  file: new File(['x'], name),
});
const knowledge = (name: string): FormFileEntry => ({
  name,
  value: `https://minio/${name}?X-Amz-Algorithm=x`,
  source: 'knowledge',
});

describe('splitLocalSources — AC-21', () => {
  it('returns only the local entries of a mixed list, in order', () => {
    const mixed = [local('a.pdf'), knowledge('b.pdf'), local('c.pdf')];
    expect(splitLocalSources(mixed).map((f) => f.name)).toEqual(['a.pdf', 'c.pdf']);
  });

  it('returns an empty list when everything came from the knowledge space', () => {
    // The caller relies on this to skip the archive entirely — no space lookup,
    // no folder creation (design 决策 7 的顺序).
    expect(splitLocalSources([knowledge('a.pdf'), knowledge('b.pdf')])).toEqual([]);
  });

  it('returns an empty list for an empty / missing input', () => {
    expect(splitLocalSources([])).toEqual([]);
    expect(splitLocalSources(null)).toEqual([]);
    expect(splitLocalSources(undefined)).toEqual([]);
  });

  it('keeps every local entry whole, including its File object', () => {
    const picked = local('a.pdf');
    const result = splitLocalSources([picked]);
    expect(result).toEqual([picked]);
    expect(result[0].file).toBeInstanceOf(File);
  });
});
