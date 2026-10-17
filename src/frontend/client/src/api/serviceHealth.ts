import request from './request';

/** Probe the same API proxy as business calls, including its response envelope. */
export async function isServiceAvailable(): Promise<boolean> {
    const response: { status_code?: number } = await request.get('/api/v1/env', { timeout: 5000 });
    return response.status_code === 200;
}
