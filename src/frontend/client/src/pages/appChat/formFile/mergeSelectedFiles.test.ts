/**
 * F065 T-02 — merge / de-duplicate the form file field's list (AC-09 ~ AC-12).
 */

import { mergeSelectedFiles } from './mergeSelectedFiles';
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

describe('mergeSelectedFiles — multi-select (AC-09 / AC-10 / AC-12)', () => {
  it('appends instead of replacing', () => {
    const merged = mergeSelectedFiles([local('a.pdf')], [local('b.pdf')], true);
    expect(merged.map((f) => f.name)).toEqual(['a.pdf', 'b.pdf']);
  });

  it('drops a name collision and keeps the existing entry untouched', () => {
    const existing = [local('a.pdf'), knowledge('b.pdf')];
    const merged = mergeSelectedFiles(existing, [knowledge('a.pdf')], true);
    expect(merged).toBe(existing); // nothing added → same reference
    expect(merged.map((f) => f.source)).toEqual(['local', 'knowledge']);
  });

  it('de-duplicates across sources in both directions', () => {
    expect(mergeSelectedFiles([knowledge('a.pdf')], [local('a.pdf')], true)).toHaveLength(1);
    expect(mergeSelectedFiles([local('a.pdf')], [knowledge('a.pdf')], true)).toHaveLength(1);
  });

  it('de-duplicates within a single incoming batch', () => {
    const merged = mergeSelectedFiles([], [local('a.pdf'), local('a.pdf'), local('b.pdf')], true);
    expect(merged.map((f) => f.name)).toEqual(['a.pdf', 'b.pdf']);
  });

  it('returns the current list by reference when nothing is added (AC-12)', () => {
    const existing = [local('a.pdf')];
    expect(mergeSelectedFiles(existing, [local('a.pdf')], true)).toBe(existing);
    expect(mergeSelectedFiles(existing, [], true)).toBe(existing);
    expect(mergeSelectedFiles(existing, null, true)).toBe(existing);
  });

  it('tolerates a missing current list', () => {
    const merged = mergeSelectedFiles(null, [local('a.pdf')], true);
    expect(merged.map((f) => f.name)).toEqual(['a.pdf']);
    expect(mergeSelectedFiles(undefined, undefined, true)).toEqual([]);
  });

  it('keeps the local File object so the archive step can re-upload it', () => {
    const merged = mergeSelectedFiles([], [local('a.pdf')], true);
    expect(merged[0].file).toBeInstanceOf(File);
  });
});

describe('mergeSelectedFiles — single-select (AC-11)', () => {
  it('keeps only the newest file', () => {
    const merged = mergeSelectedFiles([local('a.pdf')], [local('b.pdf')], false);
    expect(merged.map((f) => f.name)).toEqual(['b.pdf']);
  });

  it('does not de-duplicate by name — the same file re-picked simply wins', () => {
    const merged = mergeSelectedFiles([knowledge('a.pdf')], [local('a.pdf')], false);
    expect(merged).toHaveLength(1);
    expect(merged[0].source).toBe('local');
  });

  it('is a no-op when nothing is picked', () => {
    const existing = [local('a.pdf')];
    expect(mergeSelectedFiles(existing, [], false)).toBe(existing);
  });
});
