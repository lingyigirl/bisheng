import '@testing-library/jest-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen } from '@testing-library/react';
import { useState } from 'react';
import { getMemberToolCatalog } from '~/api/harness';
import { getSelectableSkills } from '~/api/linsight';
import type { SubagentConfig } from '~/types/harness';
import { SubagentEditor } from './SubagentEditor';

jest.mock('~/hooks', () => ({ useLocalize: () => (key: string, options?: { id?: number }) => options?.id ? `${key} ${options.id}` : key }));
jest.mock('~/api/harness', () => ({ getMemberToolCatalog: jest.fn() }));
jest.mock('~/api/linsight', () => ({ getSelectableSkills: jest.fn().mockResolvedValue([]) }));

const member: SubagentConfig = {
    id: 'test', name: 'Verifier', description: 'Verify', system_prompt: '', model_id: null,
    skill_names: [], tool_refs: [], connector_refs: [39],
};

beforeEach(() => {
    (getSelectableSkills as jest.Mock).mockResolvedValue([]);
    (getMemberToolCatalog as jest.Mock).mockResolvedValue([]);
});

function editor(disabled = false) {
    const onChange = jest.fn();
    const client = new QueryClient({ defaultOptions: { queries: { retry: false, cacheTime: 0 } } });
    function EditorDraft() {
        const [draft, setDraft] = useState(member);
        return <SubagentEditor member={draft} options={{ tools: [] }} disabled={disabled} onChange={(next) => { setDraft(next); onChange(next); }} />;
    }
    render(<QueryClientProvider client={client}><EditorDraft /></QueryClientProvider>);
    fireEvent.mouseDown(screen.getByRole('tab', { name: /harness.resourceSettings/ }), { button: 0, ctrlKey: false });
    return onChange;
}

test('shows the authorized connector absent from daily chat config and preserves its selection', async () => {
    (getMemberToolCatalog as jest.Mock).mockResolvedValue([{ id: 17, name: 'Multiplier', is_preset: 0, children: [{ id: 39, name: 'multiply' }] }]);
    const onChange = editor();
    fireEvent.mouseDown(screen.getByRole('tab', { name: /harness.connectors/ }), { button: 0, ctrlKey: false });
    expect(screen.queryByText('multiply')).not.toBeInTheDocument();
    fireEvent.click(await screen.findByRole('button', { name: /Multiplier/ }));
    const label = await screen.findByText('multiply');
    const checkbox = label.closest('label')?.querySelector('[role="checkbox"]');
    expect(checkbox).toHaveAttribute('data-state', 'checked');
    fireEvent.click(checkbox!);
    expect(onChange).toHaveBeenCalledWith({ ...member, connector_refs: [] });
});

test('groups connector actions and auto-expands groups for search matches', async () => {
    (getMemberToolCatalog as jest.Mock).mockResolvedValue([
        { id: 24, name: 'Enterprise finance', is_preset: 2, children: [{ id: 114, name: 'get_vcpe_invest' }, { id: 115, name: 'get_vcpe_exit' }] },
        { id: 36, name: 'Weather', is_preset: 0, children: [{ id: 129, name: 'getCurrentWeather' }] },
        { id: 1, name: 'Builtin', is_preset: 1, children: [{ id: 10, name: 'Calculator' }] },
    ]);
    editor();
    fireEvent.mouseDown(screen.getByRole('tab', { name: /harness.connectors/ }), { button: 0, ctrlKey: false });

    expect(await screen.findByRole('button', { name: /Enterprise finance/ })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Weather/ })).toBeInTheDocument();
    expect(screen.queryByText('get_vcpe_invest')).not.toBeInTheDocument();
    expect(screen.queryByText('Builtin')).not.toBeInTheDocument();

    fireEvent.change(screen.getByRole('searchbox'), { target: { value: 'getCurrentWeather' } });
    expect(screen.queryByRole('button', { name: /Enterprise finance/ })).not.toBeInTheDocument();
    expect(await screen.findByText('getCurrentWeather')).toBeInTheDocument();
});

test('keeps missing historical capabilities visible and read-only', async () => {
    (getMemberToolCatalog as jest.Mock).mockResolvedValue([]);
    editor(true);
    fireEvent.mouseDown(screen.getByRole('tab', { name: /harness.connectors/ }), { button: 0, ctrlKey: false });
    const label = await screen.findByText('harness.unavailableCapability 39');
    const checkbox = label.closest('label')?.querySelector('[role="checkbox"]');
    expect(checkbox).toHaveAttribute('data-state', 'checked');
    expect(checkbox).toBeDisabled();
    expect(screen.getByRole('searchbox')).toBeEnabled();
});

test('searches descriptions and group names without losing selections across categories', async () => {
    (getSelectableSkills as jest.Mock).mockResolvedValue([
        { name: 'report', display_name: 'Report writer', description: 'Export PDF' },
        { name: 'audit', display_name: 'Auditor', description: 'Review records' },
    ]);
    (getMemberToolCatalog as jest.Mock).mockResolvedValue([
        { id: 1, name: 'Arithmetic', is_preset: 1, children: [{ id: 10, name: 'Calculator' }] },
        { id: 2, name: 'Multiplier', is_preset: 0, children: [{ id: 39, name: 'multiply' }] },
    ]);
    const onChange = editor();
    await screen.findByText('Report writer');
    fireEvent.change(screen.getByRole('searchbox'), { target: { value: 'PDF' } });
    expect(screen.queryByText('Auditor')).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('checkbox', { name: 'Report writer' }));
    fireEvent.change(screen.getByRole('searchbox'), { target: { value: '' } });
    fireEvent.mouseDown(screen.getByRole('tab', { name: /harness.tools/ }), { button: 0, ctrlKey: false });
    fireEvent.change(screen.getByRole('searchbox'), { target: { value: 'Arithmetic' } });
    fireEvent.click(screen.getByRole('checkbox', { name: /Calculator/ }));
    expect(onChange).toHaveBeenLastCalledWith({ ...member, skill_names: ['report'], tool_refs: [10] });
    fireEvent.change(screen.getByRole('searchbox'), { target: { value: '' } });
    fireEvent.mouseDown(screen.getByRole('tab', { name: /harness.skills/ }), { button: 0, ctrlKey: false });
    fireEvent.click(screen.getByRole('checkbox', { name: 'harness.selectedOnly' }));
    expect(screen.getByRole('checkbox', { name: 'Report writer' })).toBeChecked();
    expect(screen.queryByText('Auditor')).not.toBeInTheDocument();
    expect(screen.getByRole('tab', { name: /harness.tools 1/ })).toBeInTheDocument();
});

test('bounds rendered rows for a large catalog and finds selections beyond the first page', async () => {
    (getSelectableSkills as jest.Mock).mockResolvedValue(Array.from({ length: 1000 }, (_, index) => ({ name: `skill-${index}`, display_name: `Skill ${index}`, description: '' })));
    (getMemberToolCatalog as jest.Mock).mockResolvedValue([]);
    editor();
    await screen.findByText('Skill 0');
    expect(screen.getAllByRole('checkbox')).toHaveLength(41);
    expect(screen.queryByText('Skill 40')).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'harness.nextPage' }));
    expect(screen.getByText('Skill 40')).toBeInTheDocument();
    expect(screen.queryByText('Skill 0')).not.toBeInTheDocument();
    fireEvent.change(screen.getByRole('searchbox'), { target: { value: 'Skill 999' } });
    fireEvent.click(screen.getByRole('checkbox', { name: 'Skill 999' }));
    fireEvent.change(screen.getByRole('searchbox'), { target: { value: '' } });
    fireEvent.click(screen.getByRole('checkbox', { name: 'harness.selectedOnly' }));
    expect(screen.getByRole('checkbox', { name: 'Skill 999' })).toBeChecked();
    expect(screen.queryByRole('button', { name: 'harness.nextPage' })).not.toBeInTheDocument();
});

test('preserves basic fields and resource selections when their panels unmount', async () => {
    (getSelectableSkills as jest.Mock).mockResolvedValue([{ name: 'report', display_name: 'Report writer', description: '' }]);
    const onChange = editor();
    await screen.findByText('Report writer');
    fireEvent.click(screen.getByRole('checkbox', { name: 'Report writer' }));
    fireEvent.mouseDown(screen.getByRole('tab', { name: 'harness.basicSettings' }), { button: 0, ctrlKey: false });
    fireEvent.change(screen.getByRole('textbox', { name: 'harness.name' }), { target: { value: 'Updated verifier' } });
    fireEvent.mouseDown(screen.getByRole('tab', { name: /harness.resourceSettings/ }), { button: 0, ctrlKey: false });
    expect(await screen.findByRole('checkbox', { name: 'Report writer' })).toBeChecked();
    expect(onChange).toHaveBeenLastCalledWith({ ...member, name: 'Updated verifier', skill_names: ['report'] });
    fireEvent.mouseDown(screen.getByRole('tab', { name: 'harness.basicSettings' }), { button: 0, ctrlKey: false });
    expect(screen.getByRole('textbox', { name: 'harness.name' })).toHaveValue('Updated verifier');
});
