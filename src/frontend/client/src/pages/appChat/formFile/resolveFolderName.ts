/**
 * F065 — the archive folder name (AC-15 / AC-16).
 *
 * The label is a MATCHING TOKEN against user-authored workflow config, not UI
 * copy: it is compared to whatever the operator typed as the field's display
 * name in the builder. Routing it through i18n would break the comparison —
 * a translated token would never equal the label the backend sends.
 */
// eslint-disable-next-line no-restricted-syntax -- matching token for a user-authored form label, not UI copy
export const CUSTOMER_NAME_LABEL = '客户名称';

import type { FormFieldLike } from './types';

/**
 * The `key` of the first form field whose display label is the customer-name
 * label, or `null` when there is none. `key` (not the label) is what indexes the
 * submitted values.
 */
export function findCustomerNameFieldKey(
  fields: readonly FormFieldLike[] | null | undefined,
): string | null {
  if (!Array.isArray(fields)) return null;
  const field = fields.find(
    (item) => typeof item?.value === 'string' && item.value.trim() === CUSTOMER_NAME_LABEL,
  );
  return field && typeof field.key === 'string' && field.key ? field.key : null;
}

/**
 * The folder to archive local uploads into (AC-15 / AC-16).
 *
 * The customer name wins when the form has such a field and its value is
 * non-blank; otherwise the application name is the fallback. `null` means "do
 * not archive": it happens only when both are blank, which is dirty data rather
 * than a user choice, so the caller skips the archive instead of creating a
 * nameless folder (spec §3).
 */
export function resolveFolderName(
  fields: readonly FormFieldLike[] | null | undefined,
  values: Record<string, unknown> | null | undefined,
  appName: string | null | undefined,
): string | null {
  const key = findCustomerNameFieldKey(fields);
  const raw = key != null ? values?.[key] : undefined;
  const customerName = typeof raw === 'string' ? raw.trim() : '';
  if (customerName) return customerName;

  const app = typeof appName === 'string' ? appName.trim() : '';
  return app || null;
}
