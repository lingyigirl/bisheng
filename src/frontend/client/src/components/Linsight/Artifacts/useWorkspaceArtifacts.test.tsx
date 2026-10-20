/**
 * F035: the inline workspace panel must show EVERY task turn's files — the
 * latest round's `file_list` alone loses inherited deliverables, because each
 * follow-up round is its own session version and the backend stops
 * re-delivering inherited-but-unchanged files.
 *
 * These tests pin both halves of useWorkspaceArtifacts:
 *  - the aggregation itself (rounds merged from the store, per-file version
 *    stamping, same-name overwrite by the later round);
 *  - the lazy hydration of rounds missing from the store (fresh page load).
 */

import { renderHook, waitFor } from '@testing-library/react';
// Recoil is frozen for PRODUCTION code; this test only mounts a root so the
// hook can read the existing linsightMapState atom (same scaffolding as
// useLinsightManager.test.tsx).
// eslint-disable-next-line no-restricted-imports
import { RecoilRoot } from 'recoil';
import { getLinsightSessionVersionList, getLinsightTaskList } from '~/api/linsight';
import { linsightMapState, type LinsightInfo } from '~/store/linsight';
import { useWorkspaceArtifacts } from './useWorkspaceArtifacts';

// Factory mocks (not automock): `sse.js` ships ESM only and is not in the jest
// transform allow-list, so merely requiring it to derive a mock shape throws.
jest.mock('sse.js', () => ({ SSE: jest.fn() }));
jest.mock('~/Providers', () => ({
    useToastContext: () => ({ showToast: jest.fn() }),
}));
jest.mock('~/utils', () => ({
    addConversation: jest.fn(),
    formatTime: jest.fn(() => ''),
    toggleNav: jest.fn(),
}));
jest.mock('~/api/linsight', () => ({
    continueLinsight: jest.fn(),
    getLinsightSessionVersionList: jest.fn(),
    getLinsightTaskList: jest.fn(),
    startLinsight: jest.fn(),
}));

const gen = (name: string, url: string) => ({
    file_id: `f-${name}`,
    file_name: name,
    file_url: url,
});

const upload = (name: string) => ({
    file_id: `u-${name}`,
    file_name: name,
    markdown_file_path: `uploads/${name}/index.md`,
    valid: true,
});

/** Minimal LinsightInfo stand-in; the store type is far wider than this hook reads. */
const sv = (over: Record<string, unknown>): LinsightInfo =>
    ({
        id: 'sv-x',
        status: 'completed',
        files: [],
        file_list: [],
        history: [],
        ...over,
    }) as unknown as LinsightInfo;

const render = (
    map: Map<string, LinsightInfo>,
    versionIds: string[],
    enabled = true,
) =>
    renderHook(() => useWorkspaceArtifacts('chat-1', versionIds, enabled, ''), {
        wrapper: ({ children }) => (
            <RecoilRoot initializeState={({ set }) => set(linsightMapState, map)}>
                {children}
            </RecoilRoot>
        ),
    });

describe('useWorkspaceArtifacts', () => {
    beforeEach(() => {
        jest.clearAllMocks();
    });

    it('merges every hydrated round — uploads first, later same-name round wins', () => {
        const map = new Map<string, LinsightInfo>([
            [
                'sv1',
                sv({
                    id: 'sv1',
                    files: [upload('data.xlsx')],
                    file_list: [gen('deck.pptx', 'sv1/deck.pptx'), gen('report.md', 'sv1/report.md')],
                }),
            ],
            ['sv2', sv({ id: 'sv2', file_list: [gen('report.md', 'sv2/report.md')] })],
        ]);

        const { result } = render(map, ['sv1', 'sv2']);

        expect(result.current.map((f) => f.file_name)).toEqual([
            'data.xlsx',
            'deck.pptx',
            'report.md',
        ]);
        // Each row resolves against the version that produced it…
        expect(result.current[0].versionId).toBe('sv1');
        expect(result.current[1].versionId).toBe('sv1');
        // …and the rewritten report is round 2's file, not a stale duplicate.
        expect(result.current[2].versionId).toBe('sv2');
        expect(result.current[2].file_url).toBe('sv2/report.md');
        expect(getLinsightSessionVersionList).not.toHaveBeenCalled();
    });

    it('lazy-hydrates rounds missing from the store once the panel opens', async () => {
        (getLinsightSessionVersionList as jest.Mock).mockResolvedValue([
            {
                id: 'sv1',
                status: 'completed',
                files: [],
                output_result: { final_files: [gen('deck.pptx', 'sv1/deck.pptx')] },
            },
            { id: 'sv2', status: 'completed', files: [], output_result: { final_files: [] } },
        ]);
        (getLinsightTaskList as jest.Mock).mockResolvedValue([]);

        const map = new Map<string, LinsightInfo>([['sv2', sv({ id: 'sv2' })]]);
        const { result } = render(map, ['sv1', 'sv2']);

        await waitFor(() =>
            expect(result.current.map((f) => f.file_name)).toEqual(['deck.pptx']),
        );
        expect(result.current[0].versionId).toBe('sv1');
        expect(getLinsightSessionVersionList).toHaveBeenCalledWith('chat-1', '');
        expect(getLinsightTaskList).toHaveBeenCalledWith('sv1', expect.anything(), '');
    });

    it('does not fetch while the panel is closed', () => {
        const map = new Map<string, LinsightInfo>([['sv2', sv({ id: 'sv2' })]]);

        render(map, ['sv1', 'sv2'], false);

        expect(getLinsightSessionVersionList).not.toHaveBeenCalled();
        expect(getLinsightTaskList).not.toHaveBeenCalled();
    });
});
