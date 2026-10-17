import '@testing-library/jest-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen } from '@testing-library/react';
import { getMemberToolCatalog } from '~/api/harness';
import type { SubagentConfig } from '~/types/harness';
import { SubagentEditor } from './SubagentEditor';

jest.mock('~/hooks', () => ({ useLocalize: () => (key: string, options?: { id?: number }) => options?.id ? `${key} ${options.id}` : key }));
jest.mock('~/api/harness', () => ({ getMemberToolCatalog: jest.fn() }));
jest.mock('~/api/linsight', () => ({ getSelectableSkills: jest.fn().mockResolvedValue([]) }));

const member: SubagentConfig = {
    id: 'test', name: 'Verifier', description: 'Verify', system_prompt: '', model_id: null,
    skill_names: [], tool_refs: [], connector_refs: [39],
};

function editor(disabled = false) {
    const onChange = jest.fn();
    const client = new QueryClient({ defaultOptions: { queries: { retry: false, cacheTime: 0 } } });
    render(<QueryClientProvider client={client}><SubagentEditor member={member} options={{ tools: [] }} disabled={disabled} onChange={onChange} /></QueryClientProvider>);
    return onChange;
}

test('shows the authorized connector absent from daily chat config and preserves its selection', async () => {
    (getMemberToolCatalog as jest.Mock).mockResolvedValue([{ id: 17, name: 'Multiplier', is_preset: 0, children: [{ id: 39, name: 'multiply' }] }]);
    const onChange = editor();
    const label = await screen.findByText('multiply');
    const checkbox = label.closest('label')?.querySelector('[role="checkbox"]');
    expect(checkbox).toHaveAttribute('data-state', 'checked');
    fireEvent.click(checkbox!);
    expect(onChange).toHaveBeenCalledWith({ ...member, connector_refs: [] });
});

test('keeps missing historical capabilities visible and read-only', async () => {
    (getMemberToolCatalog as jest.Mock).mockResolvedValue([]);
    editor(true);
    const label = await screen.findByText('harness.unavailableCapability 39');
    const checkbox = label.closest('label')?.querySelector('[role="checkbox"]');
    expect(checkbox).toHaveAttribute('data-state', 'checked');
    expect(checkbox).toBeDisabled();
});
