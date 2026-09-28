/**
 * F065 T-16 — submit wiring (AC-22 / AC-23).
 *
 * The contract this file exists to pin: archiving is fire-and-forget. It runs
 * with the form's own values, and NOTHING it does — including failing — may
 * change, delay or suppress the submit payload.
 *
 * Every child component is stubbed: the point is the wiring inside `InputForm`,
 * not how a textarea or a select renders.
 */

import { fireEvent, render, waitFor, within } from '@testing-library/react';
import type { ReactNode } from 'react';
import type { FormFileEntry } from '../formFile/types';
import InputForm from './InputForm';
import { CUSTOMER_NAME_LABEL } from '../formFile/resolveFolderName';

const mockEmit = jest.fn();
const mockArchive = jest.fn();
const mockShowToast = jest.fn();

jest.mock('~/components', () => ({
  Button: ({ children, ...props }: { children?: ReactNode }) => (
    <button data-testid="form-submit" {...props}>
      {children}
    </button>
  ),
}));
jest.mock('~/components/ui/MultiSelect', () => ({ __esModule: true, default: () => null }));
jest.mock('~/components/ui/Select', () => ({
  Select: () => null,
  SelectContent: () => null,
  SelectGroup: () => null,
  SelectItem: () => null,
  SelectTrigger: () => null,
  SelectValue: () => null,
}));
jest.mock('~/Providers', () => ({ useToastContext: () => ({ showToast: mockShowToast }) }));
jest.mock('~/hooks', () => ({ useLocalize: () => (key: string) => key }));
jest.mock('../useAreaText', () => ({
  emitAreaTextEvent: (...args: unknown[]) => mockEmit(...args),
  EVENT_TYPE: { FORM_SUBMIT: 'form_submit' },
  FileTypes: { ALL: [], IMAGE: [], FILE: [] },
}));
jest.mock('../formFile/archiveLocalFiles', () => ({
  archiveLocalFiles: (...args: unknown[]) => mockArchive(...args),
}));
jest.mock('./InputComponent', () => ({
  __esModule: true,
  default: ({ onChange }: { onChange: (value: string) => void }) => (
    <input data-testid="form-text" onChange={(event) => onChange(event.target.value)} />
  ),
}));
// The real control is covered by its own suite; here it only has to hand the
// entries up, which is the channel the archive step reads.
jest.mock('./InputFileComponent', () => ({
  __esModule: true,
  default: ({
    multiple,
    onEntriesChange,
  }: {
    multiple?: boolean;
    onEntriesChange?: (entries: FormFileEntry[]) => void;
  }) => (
    <button
      data-testid={multiple ? 'file-multi' : 'file-single'}
      onClick={() =>
        onEntriesChange?.([
          { name: `${multiple ? 'multi' : 'single'}.pdf`, value: 'local://x', source: 'local', file: new File(['x'], 'x.pdf') },
        ])
      }
    />
  ),
}));
jest.mock('./MessageBsChoose', () => ({
  MessageWarper: ({ children }: { children?: ReactNode }) => <div>{children}</div>,
}));

/** The `data` prop shape, taken from the component itself — `WorkflowNodeParam`
 *  is not exported, and the fixture is intentionally looser than it. */
type FormData = Parameters<typeof InputForm>[0]['data'];
type FormFlow = Parameters<typeof InputForm>[0]['flow'];

const schema = {
  node_id: 'node-1',
  value: [
    { id: 'n1', key: 'cust', value: CUSTOMER_NAME_LABEL, type: 'text', required: false },
    { id: 'n2', key: 'multiFile', value: 'multi', type: 'file', multiple: true, file_type: 'file', required: false },
    { id: 'n3', key: 'singleFile', value: 'single', type: 'file', multiple: false, file_type: 'file', required: false },
  ],
} as unknown as FormData;

/**
 * Scoped with `within` rather than `screen`: one case renders the form twice (to
 * compare a succeeding run against a failing one), and the second tree would
 * otherwise make every `screen` query ambiguous.
 */
const renderForm = () => {
  const utils = render(
    <InputForm data={schema} flow={{ id: 'f1', name: 'my-app' } as unknown as FormFlow} />,
  );
  const q = within(utils.container);
  // Populate both file fields, then the customer-name text field.
  fireEvent.click(q.getByTestId('file-multi'));
  fireEvent.click(q.getByTestId('file-single'));
  fireEvent.change(q.getByTestId('form-text'), { target: { value: 'acme' } });
  return { submit: () => fireEvent.click(q.getByTestId('form-submit')) };
};

beforeEach(() => {
  mockArchive.mockResolvedValue({ added: 2, skippedDuplicates: 0 });
});

describe('InputForm submit — archiving is wired and fire-and-forget', () => {
  it('archives the local entries of every file field, with the form values and app name', async () => {
    const { submit } = renderForm();
    submit();

    await waitFor(() => expect(mockArchive).toHaveBeenCalledTimes(1));
    const input = mockArchive.mock.calls[0][0];
    expect(input.files).toHaveLength(2);
    expect(input.files.every((entry: FormFileEntry) => entry.source === 'local')).toBe(true);
    expect(input.fields).toBe(schema.value);
    expect(input.values.cust).toBe('acme');
    expect(input.appName).toBe('my-app');
  });

  it('submits unchanged even when archiving rejects (AC-22 / AC-23)', async () => {
    const consoleError = jest.spyOn(console, 'error').mockImplementation(() => {});
    mockArchive.mockRejectedValue(new Error('space creation denied'));

    const { submit } = renderForm();
    submit();

    // The submit is synchronous and must already have happened.
    expect(mockEmit).toHaveBeenCalledTimes(1);
    const payload = mockEmit.mock.calls[0][0];

    await waitFor(() => expect(consoleError).toHaveBeenCalled());
    // A rejection is logged, never toasted — the input area is gone by now and a
    // toast would be unactionable (design 决策 7).
    expect(mockShowToast).not.toHaveBeenCalled();
    expect(mockEmit).toHaveBeenCalledTimes(1);
    expect(mockEmit.mock.calls[0][0]).toEqual(payload);
    expect(payload.action).toBe('form_submit');
    expect(payload.data.cust).toBe('acme');
  });

  it('sends the identical payload whether archiving succeeds or fails', async () => {
    const consoleError = jest.spyOn(console, 'error').mockImplementation(() => {});

    const succeeding = renderForm();
    succeeding.submit();
    const onSuccess = mockEmit.mock.calls[0][0];

    mockEmit.mockClear();
    mockArchive.mockRejectedValue(new Error('boom'));
    const failing = renderForm();
    failing.submit();
    await waitFor(() => expect(consoleError).toHaveBeenCalled());

    expect(mockEmit.mock.calls[0][0]).toEqual(onSuccess);
  });
});
