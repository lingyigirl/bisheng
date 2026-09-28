/**
 * F065 — the type gate behind AC-29. `suffixes` is whatever the form item
 * declares, so the "no restriction" branches matter as much as the matches.
 */

import { matchesSuffix } from './suffixMatch';

const ALL = ['.PNG', '.JPG', '.PDF', '.DOCX'];

describe('matchesSuffix — accepts what the field allows (AC-29)', () => {
  it('matches regardless of case on either side', () => {
    expect(matchesSuffix('report.PDF', ALL)).toBe(true);
    expect(matchesSuffix('report.pdf', ALL)).toBe(true);
    expect(matchesSuffix('report.pdf', ['.pdf'])).toBe(true);
  });

  it('matches a dot-less suffix entry', () => {
    expect(matchesSuffix('report.pdf', ['pdf'])).toBe(true);
  });

  it('accepts a name that carries several dots', () => {
    expect(matchesSuffix('2026.09.report.docx', ALL)).toBe(true);
  });
});

describe('matchesSuffix — rejects the rest', () => {
  it('rejects an extension outside the list', () => {
    expect(matchesSuffix('a.pdf', ['.PNG', '.JPG'])).toBe(false);
  });

  it('rejects a name with no extension', () => {
    expect(matchesSuffix('a.pdf', ['png'])).toBe(false);
    expect(matchesSuffix('report', ALL)).toBe(false);
  });

  it('does not treat a suffix as a substring', () => {
    expect(matchesSuffix('a.pdf.bak', ['.pdf'])).toBe(false);
  });
});

describe('matchesSuffix — no restriction means everything passes', () => {
  it('passes when the list is empty, missing or blank', () => {
    expect(matchesSuffix('a.pdf', [])).toBe(true);
    expect(matchesSuffix('a.pdf', undefined)).toBe(true);
    expect(matchesSuffix('a.pdf', [''])).toBe(true);
  });
});
