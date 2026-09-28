/**
 * F065 — the two rules behind the select-all row (AC-29 ~ AC-31): which rows it
 * may pick, and what the checkbox looks like. Both are pure, so the meaningful
 * edge cases (folders, type-mismatched files, an empty location) live here
 * rather than in a rendering test.
 */

import type { BrowseChild } from './useKnowledgeBrowse';
import { selectableFileIds, triState } from './selectAll';

const row = (id: string, name: string, isFolder = false): BrowseChild => ({ id, name, isFolder });

const LOCATION = [
  row('20', 'reports', true),
  row('21', 'a.pdf'),
  row('22', 'notes.txt'),
];

describe('selectableFileIds — what "all" may include (AC-29 / AC-30)', () => {
  it('skips folders and keeps the listed order', () => {
    expect(selectableFileIds(LOCATION, undefined)).toEqual(['21', '22']);
  });

  it('skips files the field would refuse', () => {
    expect(selectableFileIds(LOCATION, ['.PDF'])).toEqual(['21']);
  });

  it('selects nothing when the location holds no acceptable file', () => {
    expect(selectableFileIds(LOCATION, ['.docx'])).toEqual([]);
    expect(selectableFileIds([row('20', 'reports', true)], undefined)).toEqual([]);
  });

  it('tolerates a location that has not loaded yet', () => {
    expect(selectableFileIds(null, undefined)).toEqual([]);
    expect(selectableFileIds(undefined, ['.pdf'])).toEqual([]);
  });
});

describe('triState — what the checkbox shows', () => {
  it('is checked only when every selectable row is picked', () => {
    expect(triState(2, 2)).toBe(true);
  });

  it('is indeterminate on a partial pick', () => {
    expect(triState(1, 2)).toBe('indeterminate');
    expect(triState(1, 5)).toBe('indeterminate');
  });

  it('is unchecked when nothing is picked', () => {
    expect(triState(0, 2)).toBe(false);
  });

  it('is not vacuously "all selected" in a location with nothing to pick', () => {
    expect(triState(0, 0)).toBe(false);
  });
});
