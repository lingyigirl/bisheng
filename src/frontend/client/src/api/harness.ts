import type { HarnessOptions } from '~/types/harness';
import request from './request';

export function getReplaySnapshot(sessionVersionId: string) {
    return request.get<{ data: Record<string, unknown> }>('/api/v1/linsight/workbench/replay-snapshot', {
        params: { session_version_id: sessionVersionId },
    }).then((response) => response.data);
}

type ToolCatalogResponse = { status_code: number; data: NonNullable<HarnessOptions['tools']> };

/** The backend filters this catalog through the current user's use permission. */
export async function getMemberToolCatalog(): Promise<NonNullable<HarnessOptions['tools']>> {
    const response: ToolCatalogResponse = await request.get('/api/v1/tool', { params: { permission_id: 'use_tool' } });
    if (response.status_code !== 200) throw new Error('Could not load member capabilities');
    return response.data;
}
