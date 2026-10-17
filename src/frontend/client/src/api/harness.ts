import request from './request';

export function getReplaySnapshot(sessionVersionId: string) {
    return request.get<{ data: Record<string, unknown> }>('/api/v1/linsight/workbench/replay-snapshot', {
        params: { session_version_id: sessionVersionId },
    }).then((response) => response.data);
}
