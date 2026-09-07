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
    create_time: string;
}

export interface SensitiveWordHitLogQuery {
    direction?: string;
    flow_id?: string;
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
