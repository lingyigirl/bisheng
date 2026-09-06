/**
 * T009 — F051 VersionCompareSheet component tests.
 *
 * Covers AC-04/AC-05 (node change list + field-level old→new rendering),
 * AC-03 (same-version selection disables start + helper text) and AC-07
 * (identical versions show the empty-state message).
 */
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

const react = await vi.hoisted(async () => await import('react'));
const SelectChangeContext = react.createContext<(value: string) => void>(() => {});

const getWorkFlowVersions = vi.fn();
const compareWorkFlowVersions = vi.fn();

vi.mock('react-i18next', () => ({
    useTranslation: () => ({ t: (key: string) => key }),
}));

vi.mock('@/controllers/API/workflow', () => ({
    getWorkFlowVersions: (...args: unknown[]) => getWorkFlowVersions(...args),
    compareWorkFlowVersions: (...args: unknown[]) => compareWorkFlowVersions(...args),
}));

vi.mock('@/controllers/request', () => ({
    captureAndAlertRequestErrorHoc: (p: Promise<unknown>) => p,
}));

vi.mock('@/components/bs-ui/sheet', () => ({
    Sheet: ({ children, open }: { children: React.ReactNode; open: boolean }) =>
        open ? <div>{children}</div> : null,
    SheetContent: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
    SheetHeader: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
    SheetTitle: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
}));

vi.mock('@/components/bs-ui/select', () => ({
    Select: ({ children, onValueChange }: { children: React.ReactNode; onValueChange?: (v: string) => void }) => (
        <SelectChangeContext.Provider value={onValueChange ?? (() => {})}>
            {children}
        </SelectChangeContext.Provider>
    ),
    SelectTrigger: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
    SelectValue: ({ children, placeholder }: { children?: React.ReactNode; placeholder?: string }) => (
        <div>{children ?? placeholder}</div>
    ),
    SelectContent: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
    SelectItem: ({ children, value }: { children: React.ReactNode; value: string }) => {
        const onValueChange = react.useContext(SelectChangeContext);
        return (
            <button type="button" data-testid={`opt-${value}`} onClick={() => onValueChange(value)}>
                {children}
            </button>
        );
    },
}));

vi.mock('@/components/bs-ui/button', () => ({
    Button: ({ children, disabled, onClick }: { children: React.ReactNode; disabled?: boolean; onClick?: () => void }) => (
        <button type="button" disabled={disabled} onClick={onClick}>
            {children}
        </button>
    ),
}));

vi.mock('@/components/bs-ui/badge', () => ({
    Badge: ({ children }: { children: React.ReactNode }) => <span>{children}</span>,
}));

import { VersionCompareSheet } from '@/pages/BuildPage/flow/VersionCompareSheet';

function makeVersion(id: number, name: string, is_current = 0): Record<string, unknown> {
    return {
        id,
        name,
        is_current,
        is_delete: 0,
        flow_id: 'f1',
        create_time: '2026-09-01 10:00:00',
        update_time: '2026-09-01 10:00:00',
        description: null,
        data: null,
    };
}

const VERSIONS = [makeVersion(10, 'v1'), makeVersion(20, 'v2', 1)];

const MODIFIED_FIXTURE = {
    from_version: { id: 10, name: 'v1', update_time: '' },
    to_version: { id: 20, name: 'v2', update_time: '' },
    changed: true,
    node_changes: [
        {
            node_id: 'a',
            node_name: 'NodeA',
            node_type: 'llm',
            change: 'modified',
            fields: [{ path: 'group_params[0].params[model].value', old_value: 'gpt', new_value: 'deepseek' }],
        },
        { node_id: 'b', node_name: 'NodeB', node_type: 'code', change: 'added' },
        { node_id: 'c', node_name: 'NodeC', node_type: 'llm', change: 'removed' },
    ],
    edge_changes: [
        { source: 'a', target: 'b', sourceHandle: 'right_handle', targetHandle: 'left_handle', change: 'added' },
    ],
};

const IDENTICAL_FIXTURE = {
    from_version: { id: 10, name: 'v1', update_time: '' },
    to_version: { id: 20, name: 'v2', update_time: '' },
    changed: false,
    node_changes: [],
    edge_changes: [],
};

function renderSheet(currentVersionId = 20) {
    return render(
        <VersionCompareSheet
            open={true}
            onOpenChange={() => {}}
            flowId="f1"
            currentVersionId={currentVersionId}
            onLocateNode={() => {}}
        />,
    );
}

async function startComparison() {
    fireEvent.click(screen.getByText('skills.compareStart'));
    await waitFor(() => expect(compareWorkFlowVersions).toHaveBeenCalled());
}

describe('VersionCompareSheet', () => {
    beforeEach(() => {
        vi.clearAllMocks();
        getWorkFlowVersions.mockResolvedValue({ data: VERSIONS, total: 2 });
        compareWorkFlowVersions.mockResolvedValue(MODIFIED_FIXTURE);
    });

    it('renders node added/removed/modified with field old→new values (AC-04/AC-05)', async () => {
        renderSheet();
        await waitFor(() => expect(getWorkFlowVersions).toHaveBeenCalled());
        await act(async () => {
            await startComparison();
        });

        expect(screen.getByText('skills.compareNodeAdded')).toBeTruthy();
        expect(screen.getByText('skills.compareNodeRemoved')).toBeTruthy();
        expect(screen.getByText('skills.compareNodeModified')).toBeTruthy();
        expect(screen.getByText('NodeA')).toBeTruthy();
        expect(screen.getByText('skills.compareEdgeAdded')).toBeTruthy();
        // field-level old → new
        expect(screen.getByText('gpt')).toBeTruthy();
        expect(screen.getByText('deepseek')).toBeTruthy();
        expect(screen.getByText('group_params[0].params[model].value')).toBeTruthy();
        // defaults per AC-02: old=v1 (10), new=v2 (20)
        expect(compareWorkFlowVersions).toHaveBeenCalledWith({
            flow_id: 'f1',
            from_version_id: 10,
            to_version_id: 20,
        });
    });

    it('disables start and shows helper text when the same version is picked (AC-03)', async () => {
        renderSheet();
        await waitFor(() => expect(getWorkFlowVersions).toHaveBeenCalled());
        // Pick v2 as the OLD version (first select's option), matching new=v2.
        fireEvent.click(screen.getAllByTestId('opt-20')[0]);

        const button = screen.getByText('skills.compareStart').closest('button');
        expect(button && (button as HTMLButtonElement).disabled).toBe(true);
        expect(screen.getByText('skills.compareSelectDifferentVersions')).toBeTruthy();
        expect(compareWorkFlowVersions).not.toHaveBeenCalled();
    });

    it('shows the identical-content message when changed is false (AC-07)', async () => {
        compareWorkFlowVersions.mockResolvedValue(IDENTICAL_FIXTURE);
        renderSheet();
        await waitFor(() => expect(getWorkFlowVersions).toHaveBeenCalled());
        await act(async () => {
            await startComparison();
        });

        expect(screen.getByText('skills.compareIdentical')).toBeTruthy();
    });
});
