/**
 * F065 — "may this file be picked at all", judged by the form item's own
 * file-type list (AC-29).
 *
 * The picker receives the same `suffixes` the local `<input accept>` gets
 * (`FileTypes[type]`: uppercase and dotted, e.g. `.PDF`), so a file the field
 * would refuse on upload cannot be picked out of a knowledge space either.
 *
 * An empty list means "this field declares no restriction" — everything
 * matches. That is also what an unrecognised `file_type` degrades to, so a
 * missing list can never grey out the whole dialog.
 */

/** Case-insensitive `a.pdf` vs `.PDF`; a dot-less `pdf` entry matches too. */
export function matchesSuffix(name: string, suffixes: readonly string[] | undefined): boolean {
  const list = (suffixes ?? []).filter((suffix) => !!suffix);
  if (!list.length) return true;
  const lowerName = (name ?? '').toLowerCase();
  return list.some((suffix) => lowerName.endsWith(suffix.toLowerCase()));
}
