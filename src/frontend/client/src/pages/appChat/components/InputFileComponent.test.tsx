/**
 * F065 Wave C — the form file control (AC-01, AC-02, AC-03, AC-04, AC-05,
 * AC-08, AC-09 ~ AC-13, AC-23).
 *
 * The control builds its `<input type="file">` imperatively, so the local-upload
 * cases capture that element and fire `change` on it directly — React isn't
 * involved (the handler is a DOM `onchange` property), so the element never needs
 * to be attached to the document.
 *
 * The knowledge-space dialog is stubbed: its own behaviour is covered by
 * `KnowledgeFilePicker/index.test.tsx`. What matters here is only what the
 * control does with a confirmed selection.
 *
 * NOTE — AC-13's "local entries keep their `File`" is asserted in Wave D, where
 * the archive step actually consumes it (see tasks 偏差记录).
 *
 * Assertions avoid jest-dom matchers (`toBeInTheDocument` & co): `tsconfig.json`
 * declares `types` OUTSIDE `compilerOptions`, so their types are never loaded and
 * they fail `tsc-strict` in any test file that isn't `@ts-strict-ignore`-frozen —
 * and new files may not add that header. `getBy*` already throws when absent, so
 * plain assertions carry the same meaning.
 */

import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import InputFileComponent from './InputFileComponent';
import type { FormFileEntry } from '../formFile/types';

const mockUploadFileWithProgress = jest.fn();
const mockShowToast = jest.fn();
const mockResolvePickedValues = jest.fn();

jest.mock('recoil', () => ({
  useRecoilState: () => [{ uploaded_files_maximum_size: 50 }, jest.fn()],
}));
jest.mock('../store/atoms', () => ({ bishengConfState: {} }));
jest.mock('~/hooks/useLocalize', () => ({
  __esModule: true,
  default: () => (key: string, options?: Record<string, unknown>) =>
    options ? `${key}:${JSON.stringify(options)}` : key,
}));
jest.mock('~/Providers/ToastContext', () => ({
  useToastContext: () => ({ showToast: mockShowToast }),
}));
jest.mock('~/api/apps', () => ({
  uploadFileWithProgress: (...args: unknown[]) => mockUploadFileWithProgress(...args),
  uploadFile: jest.fn(),
}));
jest.mock('./KnowledgeFilePicker/resolvePickedValues', () => ({
  resolvePickedValues: (...args: unknown[]) => mockResolvePickedValues(...args),
}));
// The stub records the props it is handed — the dialog renders nothing when
// closed, and what it receives (bar the callbacks) is worth asserting.
let mockStubPickerProps: Record<string, unknown> | null = null;

// Radix dialogs render into a portal and are awkward to drive here; the stub
// exposes the two callbacks the control actually wires up.
jest.mock('./KnowledgeFilePicker', () => {
  // `jest.requireActual` rather than `require`: a jest.mock factory may not
  // reference out-of-scope imports, and a bare `require` trips
  // `@typescript-eslint/no-require-imports`.
  const React = jest.requireActual('react');
  return {
    KnowledgeFilePicker: (props: {
      open: boolean;
      onClose: () => void;
      onConfirm: (picked: unknown[]) => void;
    }) => {
      mockStubPickerProps = props as unknown as Record<string, unknown>;
      return props.open
        ? React.createElement(
            'div',
            null,
            React.createElement(
              'button',
              {
                'data-testid': 'stub-picker-confirm',
                onClick: () => props.onConfirm([{ spaceId: '7', fileId: '1', name: 'from-space.pdf' }]),
              },
              'confirm',
            ),
            React.createElement(
              'button',
              { 'data-testid': 'stub-picker-cancel', onClick: props.onClose },
              'cancel',
            ),
          )
        : null;
    },
  };
});

const file = (name: string) => new File(['x'], name, { type: 'application/pdf' });

let createdInput: HTMLInputElement | null = null;
const realCreateElement = document.createElement.bind(document);

beforeEach(() => {
  createdInput = null;
  mockStubPickerProps = null;
  jest.spyOn(document, 'createElement').mockImplementation((tagName: string) => {
    const element = realCreateElement(tagName);
    if (tagName.toLowerCase() === 'input') createdInput = element as HTMLInputElement;
    return element;
  });
  mockUploadFileWithProgress.mockImplementation((uploaded: File) =>
    Promise.resolve({ file_path: `local://${uploaded.name}` }),
  );
  mockResolvePickedValues.mockResolvedValue({
    entries: [{ name: 'from-space.pdf', value: 'https://minio/k', source: 'knowledge' } as FormFileEntry],
    failed: [],
  });
});

const renderControl = (props: Partial<Parameters<typeof InputFileComponent>[0]> = {}) => {
  const onChange = jest.fn();
  const onFileChange = jest.fn();
  render(
    <InputFileComponent
      isSSO
      onChange={onChange}
      onFileChange={onFileChange}
      flow={{ id: 'flow-1' }}
      {...props}
    />,
  );
  return { onChange, onFileChange };
};

/** Drive the imperatively-created file input. */
const pickLocal = async (...names: string[]) => {
  fireEvent.click(screen.getByTestId('file-pick-trigger'));
  expect(createdInput).not.toBeNull();
  Object.defineProperty(createdInput, 'files', { configurable: true, value: names.map(file) });
  // `act` because the handler is a raw DOM listener: without it React warns that
  // the resulting state update happened outside its batching scope.
  await act(async () => {
    createdInput!.dispatchEvent(new Event('change'));
  });
  await waitFor(() => expect(mockUploadFileWithProgress).toHaveBeenCalled());
};

describe('local uploads — merge and de-duplicate (AC-09 ~ AC-12)', () => {
  it('accumulates across separate picks in multi-select', async () => {
    const { onChange } = renderControl({ multiple: true, knowledgePicker: false, suffixes: ['.pdf'] });
    await pickLocal('a.pdf');
    await waitFor(() => expect(onChange).toHaveBeenLastCalledWith(['a.pdf']));

    await pickLocal('b.pdf');
    await waitFor(() => expect(onChange).toHaveBeenLastCalledWith(['a.pdf', 'b.pdf']));
  });

  it('drops a same-named local file instead of adding it twice', async () => {
    const { onChange } = renderControl({ multiple: true, knowledgePicker: false, suffixes: ['.pdf'] });
    await pickLocal('a.pdf');
    await waitFor(() => expect(onChange).toHaveBeenLastCalledWith(['a.pdf']));

    await pickLocal('a.pdf');
    await waitFor(() => expect(onChange).toHaveBeenLastCalledWith(['a.pdf']));
  });

  it('keeps only the newest file in single-select (AC-11)', async () => {
    const { onChange } = renderControl({ knowledgePicker: false, suffixes: ['.pdf'] });
    await pickLocal('a.pdf');
    await waitFor(() => expect(onChange).toHaveBeenLastCalledWith('a.pdf'));

    await pickLocal('b.pdf');
    await waitFor(() => expect(onChange).toHaveBeenLastCalledWith('b.pdf'));
  });
});

describe('callback shapes stay as the callers expect (AC-23 / AC-28)', () => {
  it('publishes a scalar in single-select', async () => {
    const { onChange, onFileChange } = renderControl({ knowledgePicker: false, suffixes: ['.pdf'] });
    await pickLocal('a.pdf');
    await waitFor(() => expect(onChange).toHaveBeenLastCalledWith('a.pdf'));
    expect(onFileChange).toHaveBeenLastCalledWith('local://a.pdf');
  });

  it('publishes arrays in multi-select', async () => {
    const { onChange, onFileChange } = renderControl({ multiple: true, knowledgePicker: false, suffixes: ['.pdf'] });
    await pickLocal('a.pdf');
    await waitFor(() => expect(onChange).toHaveBeenLastCalledWith(['a.pdf']));
    expect(onFileChange).toHaveBeenLastCalledWith(['local://a.pdf']);
  });
});

describe('knowledge-space opt-in (AC-01 / AC-28)', () => {
  it('is absent by default — the shared control must not grow the entry for other forms', () => {
    renderControl({ knowledgePicker: false });
    expect(screen.queryByTestId('knowledge-picker-trigger')).toBeNull();
  });

  it('appears only when the form opts in', () => {
    renderControl({ knowledgePicker: true });
    expect(screen.getByTestId('knowledge-picker-trigger')).toBeTruthy();
  });

  it('opens the picker with the field multi-select setting', () => {
    renderControl({ multiple: true, knowledgePicker: true });
    fireEvent.click(screen.getByTestId('knowledge-picker-trigger'));
    expect(screen.getByTestId('stub-picker-confirm')).toBeTruthy();
  });

  it('hands the picker the field’s own file-type list (AC-29)', () => {
    renderControl({ knowledgePicker: true, suffixes: ['.PDF', '.DOCX'] });
    // Read off the last render: the trigger has already mounted the dialog.
    expect(mockStubPickerProps?.suffixes).toEqual(['.PDF', '.DOCX']);
  });
});

describe('knowledge-space picks (AC-04 / AC-05 / AC-07 / AC-29)', () => {
  it('adds the resolved file to the field', async () => {
    const { onChange, onFileChange } = renderControl({ knowledgePicker: true });
    fireEvent.click(screen.getByTestId('knowledge-picker-trigger'));
    fireEvent.click(screen.getByTestId('stub-picker-confirm'));

    await waitFor(() => expect(onChange).toHaveBeenLastCalledWith('from-space.pdf'));
    expect(onFileChange).toHaveBeenLastCalledWith('https://minio/k');
  });

  it('changes nothing when the picker is cancelled (AC-05)', async () => {
    const { onChange } = renderControl({ knowledgePicker: true });
    fireEvent.click(screen.getByTestId('knowledge-picker-trigger'));
    fireEvent.click(screen.getByTestId('stub-picker-cancel'));

    expect(onChange).not.toHaveBeenCalled();
  });

  it('merges a knowledge-space pick into an existing local pick (AC-09)', async () => {
    const { onChange } = renderControl({ multiple: true, knowledgePicker: true, suffixes: ['.pdf'] });
    await pickLocal('a.pdf');
    await waitFor(() => expect(onChange).toHaveBeenLastCalledWith(['a.pdf']));

    fireEvent.click(screen.getByTestId('knowledge-picker-trigger'));
    fireEvent.click(screen.getByTestId('stub-picker-confirm'));
    await waitFor(() => expect(onChange).toHaveBeenLastCalledWith(['a.pdf', 'from-space.pdf']));
  });

  it('reports a file whose download address could not be resolved (AC-07)', async () => {
    mockResolvePickedValues.mockResolvedValue({ entries: [], failed: ['broken.pdf'] });
    const { onChange } = renderControl({ knowledgePicker: true });
    fireEvent.click(screen.getByTestId('knowledge-picker-trigger'));
    fireEvent.click(screen.getByTestId('stub-picker-confirm'));

    await waitFor(() => expect(mockShowToast).toHaveBeenCalled());
    expect(mockShowToast.mock.calls[0][0].message).toContain('com_knowledge.pick_resolve_failed');
    expect(onChange).not.toHaveBeenCalled();
  });
});
