/**
 * F065 T-01 — archive folder name resolution (AC-15 / AC-16).
 *
 * Fixture labels/values are ASCII on purpose: the only Chinese token involved is
 * `CUSTOMER_NAME_LABEL` itself, which is imported rather than spelled out (the
 * lint rule banning Chinese literals is about UI copy, and re-typing the token
 * here would just create a second place to typo it). The token is pinned once,
 * below, so a wrong constant still fails loudly.
 */

import {
  CUSTOMER_NAME_LABEL,
  findCustomerNameFieldKey,
  resolveFolderName,
} from './resolveFolderName';
import type { FormFieldLike } from './types';

const field = (key: string, label: string): FormFieldLike => ({ key, value: label });

describe('resolveFolderName — AC-15 / AC-16', () => {
  it('pins the matching token so a stray edit cannot silently stop matching', () => {
    // eslint-disable-next-line no-restricted-syntax -- pinning the matching token, not UI copy
    expect(CUSTOMER_NAME_LABEL).toBe('客户名称');
  });

  it('uses the customer-name field value when it is present and non-blank', () => {
    const fields = [field('remark', 'remark'), field('cust', CUSTOMER_NAME_LABEL)];
    expect(resolveFolderName(fields, { remark: 'ignored', cust: 'acme' }, 'my-app')).toBe('acme');
  });

  it('trims the customer-name value and the app name', () => {
    const fields = [field('cust', CUSTOMER_NAME_LABEL)];
    expect(resolveFolderName(fields, { cust: '  acme  ' }, 'my-app')).toBe('acme');
    expect(resolveFolderName([field('x', 'remark')], {}, '  my-app  ')).toBe('my-app');
  });

  it('falls back to the app name when there is no customer-name field', () => {
    expect(resolveFolderName([field('x', 'remark')], { x: 'y' }, 'my-app')).toBe('my-app');
  });

  it('falls back to the app name when the customer-name value is blank', () => {
    const fields = [field('cust', CUSTOMER_NAME_LABEL)];
    for (const blank of ['', '   ', '\t\n']) {
      expect(resolveFolderName(fields, { cust: blank }, 'my-app')).toBe('my-app');
    }
  });

  it('treats a missing / non-string customer-name value as blank', () => {
    const fields = [field('cust', CUSTOMER_NAME_LABEL)];
    expect(resolveFolderName(fields, {}, 'my-app')).toBe('my-app');
    expect(resolveFolderName(fields, { cust: 42 }, 'my-app')).toBe('my-app');
  });

  it('returns null when both the customer name and the app name are blank', () => {
    expect(resolveFolderName([field('x', 'remark')], {}, '   ')).toBeNull();
    expect(resolveFolderName([field('x', 'remark')], {}, null)).toBeNull();
    expect(resolveFolderName([field('x', 'remark')], {}, undefined)).toBeNull();
  });

  it('matches the label after trimming it', () => {
    const fields = [field('cust', `  ${CUSTOMER_NAME_LABEL}  `)];
    expect(resolveFolderName(fields, { cust: 'acme' }, 'my-app')).toBe('acme');
  });

  it('ignores a field whose label merely contains the token', () => {
    const fields = [field('cust', `pre-${CUSTOMER_NAME_LABEL}`)];
    expect(resolveFolderName(fields, { cust: 'acme' }, 'my-app')).toBe('my-app');
  });

  it('does not throw on missing / malformed inputs', () => {
    expect(resolveFolderName(null, null, null)).toBeNull();
    expect(resolveFolderName(undefined, undefined, 'my-app')).toBe('my-app');
  });
});

describe('findCustomerNameFieldKey — the shared predicate', () => {
  it('returns the key of the customer-name field', () => {
    const fields = [field('remark', 'remark'), field('cust', CUSTOMER_NAME_LABEL)];
    expect(findCustomerNameFieldKey(fields)).toBe('cust');
  });

  it('returns null when the field exists but carries no usable key', () => {
    expect(findCustomerNameFieldKey([{ value: CUSTOMER_NAME_LABEL }])).toBeNull();
    expect(findCustomerNameFieldKey([{ key: '', value: CUSTOMER_NAME_LABEL }])).toBeNull();
  });

  it('returns null when there is no such field', () => {
    expect(findCustomerNameFieldKey([field('a', 'a')])).toBeNull();
    expect(findCustomerNameFieldKey(null)).toBeNull();
  });
});
