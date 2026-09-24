/**
 * F065 — shared shapes for the form file field.
 *
 * Recoil is frozen (client `AGENTS.md`), so this directory is deliberately
 * Recoil-free: the merge rules live in pure functions that both the input
 * component and the submit-time archive step can call, and that unit tests can
 * pin without rendering anything.
 */

/** Where a selected file came from. Only `local` files are archived (AC-21). */
export type FormFileSource = 'local' | 'knowledge';

/** One selected file of a form file field. */
export interface FormFileEntry {
  /** Display name — also the de-duplication key in multi-select mode (AC-10). */
  name: string;
  /** The value handed to the workflow: a downloadable address (design 决策 3). */
  value: string;
  source: FormFileSource;
  /**
   * The original File, kept only for local entries so the archive step can
   * re-upload it and pre-check duplicates against the target space (design
   * 决策 5 / 坑 12). Dropping it right after the first upload — which is what the
   * existing SSO path does — makes that pre-check impossible.
   */
  file?: File;
}

/** A form item as the workflow's `input_schema` delivers it. */
export interface FormFieldLike {
  /** Stable field key; also the key the submitted form data is indexed by. */
  key?: unknown;
  /** The field's display label — the backend puts it in `value` (see `InputForm`). */
  value?: unknown;
}
