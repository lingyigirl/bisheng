import '@testing-library/jest-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { deleteSavedMember, listSavedMembers, saveMember, setMemberDefault } from '~/api/linsight';
import type { AgentConfig, SavedMember, SubagentConfig } from '~/types/harness';
import { SubagentPicker } from './SubagentPicker';

jest.mock('~/hooks', () => ({ useLocalize: () => (key: string) => key }));
jest.mock('./SubagentEditor', () => ({ SubagentEditor: () => <div>editor</div> }));
jest.mock('~/Providers', () => ({ useToastContext: () => ({ showToast: jest.fn() }) }));
jest.mock('~/api/linsight', () => ({
    listSavedMembers: jest.fn(),
    saveMember: jest.fn(),
    deleteSavedMember: jest.fn(),
    setMemberDefault: jest.fn(),
}));

const mockedList = listSavedMembers as jest.Mock;
const mockedSave = saveMember as jest.Mock;
const mockedDelete = deleteSavedMember as jest.Mock;
const mockedDefault = setMemberDefault as jest.Mock;

// Radix dropdowns open on pointerdown, and jsdom ships neither PointerEvent nor
// the pointer-capture methods Radix probes for — same polyfills as
// `components/Chat/Input/ChatKnowledge.test.tsx` / `components/ui/Select.test.tsx`.
beforeAll(() => {
    if (!window.PointerEvent) {
        Object.defineProperty(window, 'PointerEvent', { configurable: true, value: MouseEvent });
    }
    if (!Element.prototype.scrollIntoView) {
        Element.prototype.scrollIntoView = jest.fn();
    }
    if (!Element.prototype.hasPointerCapture) {
        Element.prototype.hasPointerCapture = jest.fn(() => false);
    }
    if (!Element.prototype.setPointerCapture) {
        Element.prototype.setPointerCapture = jest.fn();
    }
    if (!Element.prototype.releasePointerCapture) {
        Element.prototype.releasePointerCapture = jest.fn();
    }
});

const savedMember: SavedMember = {
    id: 5,
    name: 'Saved analyst',
    description: 'does numbers',
    system_prompt: '',
    model_id: null,
    skill_names: [],
    tool_refs: [],
    connector_refs: [],
    is_default: false,
    issues: {},
};

const checkedDraft: SubagentConfig = {
    id: 'lib-5',
    name: 'Saved analyst',
    description: 'does numbers',
    system_prompt: '',
    model_id: null,
    skill_names: [],
    tool_refs: [],
    connector_refs: [],
};

const emptyConfig: AgentConfig = { schema_version: 1, subagents: [] };

beforeEach(() => {
    mockedList.mockReset().mockResolvedValue([savedMember]);
    mockedSave.mockReset().mockResolvedValue(savedMember);
    mockedDelete.mockReset().mockResolvedValue({ data: { deleted: true } });
    mockedDefault.mockReset().mockResolvedValue({ data: { updated: true } });
});

function renderPicker(ui: React.ReactElement) {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    return render(<QueryClientProvider client={client}>{ui}</QueryClientProvider>);
}

/** Radix dropdowns open on pointerdown (see ChatKnowledge.test.tsx). */
const openMenu = () =>
    fireEvent.pointerDown(screen.getByTestId('collaborators-trigger'), { button: 0, ctrlKey: false });

test('dropdown lists the library and offers creating a member', async () => {
    renderPicker(<SubagentPicker value={emptyConfig} options={{}} onChange={jest.fn()} />);
    openMenu();

    expect(await screen.findByText('Saved analyst')).toBeInTheDocument();
    expect(screen.getByText('harness.newMember')).toBeInTheDocument();
    expect(screen.getByText('harness.collaboratorHint')).toBeInTheDocument();
});

test('a member without tools or connectors carries the no-tools badge', async () => {
    renderPicker(<SubagentPicker value={emptyConfig} options={{}} onChange={jest.fn()} />);
    openMenu();

    expect(await screen.findByTestId('member-no-tools')).toHaveTextContent('harness.noTools');
});

test('a member with connectors is not badged as tool-less', async () => {
    mockedList.mockResolvedValue([{ ...savedMember, connector_refs: [3] }]);
    renderPicker(<SubagentPicker value={emptyConfig} options={{}} onChange={jest.fn()} />);
    openMenu();

    expect(await screen.findByText('Saved analyst')).toBeInTheDocument();
    expect(screen.queryByTestId('member-no-tools')).not.toBeInTheDocument();
});

test('checkbox adds a saved member to the task', async () => {
    const onChange = jest.fn();
    renderPicker(<SubagentPicker value={emptyConfig} options={{}} onChange={onChange} />);
    openMenu();

    fireEvent.click(await screen.findByRole('checkbox', { name: 'Saved analyst' }));
    const added = onChange.mock.calls[0][0] as AgentConfig;
    expect(added.subagents).toHaveLength(1);
    expect(added.subagents[0].id).toBe('lib-5');
    expect(added.subagents[0].description).toBe('does numbers');
});

test('checkbox removes a checked member again', async () => {
    const onChange = jest.fn();
    renderPicker(<SubagentPicker value={{ schema_version: 1, subagents: [checkedDraft] }} options={{}} onChange={onChange} />);
    openMenu();
    fireEvent.click(await screen.findByRole('checkbox', { name: 'Saved analyst' }));
    expect((onChange.mock.calls[0][0] as AgentConfig).subagents).toHaveLength(0);
});

test('star toggles the auto-carry default', async () => {
    renderPicker(<SubagentPicker value={emptyConfig} options={{}} onChange={jest.fn()} />);
    openMenu();

    fireEvent.click(await screen.findByRole('button', { name: 'harness.defaultHint' }));
    await waitFor(() => expect(mockedDefault).toHaveBeenCalledWith(5, true));
});

test('editing a saved member saves back under its library id', async () => {
    renderPicker(<SubagentPicker value={{ schema_version: 1, subagents: [checkedDraft] }} options={{}} onChange={jest.fn()} />);
    openMenu();

    fireEvent.click(await screen.findByRole('button', { name: 'harness.edit' }));
    fireEvent.click(screen.getByRole('button', { name: 'harness.save' }));

    await waitFor(() => expect(mockedSave).toHaveBeenCalledTimes(1));
    const payload = mockedSave.mock.calls[0][0] as { id?: number; name: string };
    expect(payload.id).toBe(5);
    expect(payload.name).toBe('Saved analyst');
});

test('deleting from the editor removes the member and unchecks it', async () => {
    const onChange = jest.fn();
    renderPicker(<SubagentPicker value={{ schema_version: 1, subagents: [checkedDraft] }} options={{}} onChange={onChange} />);
    openMenu();

    fireEvent.click(await screen.findByRole('button', { name: 'harness.edit' }));
    fireEvent.click(screen.getByRole('button', { name: 'harness.deleteSaved' }));

    await waitFor(() => expect(mockedDelete).toHaveBeenCalledWith(5));
    await waitFor(() => expect((onChange.mock.calls.at(-1)?.[0] as AgentConfig).subagents).toHaveLength(0));
});

test('legacy drafts not in the library stay visible and removable', async () => {
    const legacy: SubagentConfig = { ...checkedDraft, id: 'agent-legacy', name: 'Legacy member' };
    const onChange = jest.fn();
    renderPicker(<SubagentPicker value={{ schema_version: 1, subagents: [legacy] }} options={{}} onChange={onChange} />);
    openMenu();

    const box = await screen.findByRole('checkbox', { name: 'Legacy member' });
    expect(box).toHaveAttribute('data-state', 'checked');
    fireEvent.click(box);
    expect((onChange.mock.calls[0][0] as AgentConfig).subagents).toHaveLength(0);
});

test('summary variant renders chips and opens the read-only sheet', () => {
    const value: AgentConfig = {
        schema_version: 1,
        subagents: [{ ...checkedDraft, id: 'research', name: 'Research' }],
    };
    renderPicker(<SubagentPicker value={value} options={{}} disabled variant="summary" />);

    expect(screen.getByTestId('collaborators-summary')).toBeInTheDocument();
    fireEvent.click(screen.getByText('Research'));
    expect(screen.getByText('harness.readOnly')).toBeInTheDocument();
});

test('a locked (disabled) task has no library dropdown', () => {
    renderPicker(<SubagentPicker value={{ schema_version: 1, subagents: [checkedDraft] }} options={{}} disabled />);

    expect(screen.queryByTestId('collaborators-trigger')).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /harness.members/ }));
    expect(screen.getByText('harness.readOnly')).toBeInTheDocument();
    expect(screen.queryByText('harness.newMember')).not.toBeInTheDocument();
});
