import axios from "../request";

export interface SensitiveWordHit {
    word: string;
    count: number;
}

export interface SensitiveWordHitLogItem {
    id: number;
    business_type: string;
    direction: string;
    flow_id: string | null;
    chat_id: string | null;
    user_id: number | null;
    hits: SensitiveWordHit[];
    action: string;
    // F059: which layer produced the hit. Platform rows arrive with `hits`
    // already stripped by the server (the words are authoritative content the
    // tenant may not read); `hit_count` still says how many matched.
    source: string;
    tenant_id: number | null;
    hit_count: number;
    create_time: string;
}

export interface SensitiveWordHitLogQuery {
    direction?: string;
    flow_id?: string;
    source?: string;
    tenant_id?: number;
    start_time?: string;
    end_time?: string;
    page?: number;
    page_size?: number;
}

export interface SensitiveWordHitLogPage {
    data: SensitiveWordHitLogItem[];
    total: number;
}

// 响应拦截器已解包统一 envelope, 运行时直接返回 PageData payload（勿二次 .data 解包）
export function getSensitiveWordHitLogsApi(query: SensitiveWordHitLogQuery = {}) {
    return axios.get(`/api/v1/sensitive-word-hit-logs`, {
        params: query,
    }) as unknown as Promise<SensitiveWordHitLogPage>;
}

/**
 * Cross-tenant hit log for the platform admin (F059, spec AC-20).
 *
 * A separate call from the tenant-scoped one above: that path filters to the
 * caller's own tenant AND blanks platform hit words, which is right for a tenant
 * administrator but wrong for the platform admin who owns the library — they
 * would see one tenant's rows with the words they are trying to audit removed.
 */
export function getAllSensitiveWordHitLogsApi(query: SensitiveWordHitLogQuery = {}) {
    return axios.get(`/api/v1/sensitive-word-hit-logs/all`, {
        params: query,
    }) as unknown as Promise<SensitiveWordHitLogPage>;
}
