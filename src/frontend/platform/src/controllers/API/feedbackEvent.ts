import axios from "../request";

// 响应拦截器已解包统一 envelope, 运行时直接返回业务 payload（勿二次 .data 解包）

export interface FeedbackEvent {
    id: number;
    tenant_id: number;
    user_id: number | null;
    message_id: number;
    flow_id: string | null;
    /** 0 not rated / 1 liked / 2 disliked — the value BEFORE this change. */
    from_value: number;
    /** The value AFTER this change. */
    to_value: number;
    comment: string | null;
    create_time: string | null;
}

export interface FeedbackEventPage {
    data: FeedbackEvent[];
    total: number;
}

export interface FeedbackEventQuery {
    flow_id?: string;
    feedback_type?: number;
    start_time?: string;
    end_time?: string;
    page?: number;
    page_size?: number;
}

export function getFeedbackEventsApi(query: FeedbackEventQuery = {}) {
    return axios.get(`/api/v1/message-feedback-events`, { params: query }) as unknown as Promise<FeedbackEventPage>;
}

/**
 * Cross-tenant feedback trail for the platform admin (F059, spec AC-26).
 *
 * The tenant-scoped call above narrows to the caller's own tenant; a platform
 * admin needs every tenant's rows, optionally narrowed by `tenant_id`.
 */
export function getAllFeedbackEventsApi(query: FeedbackEventQuery = {}) {
    return axios.get(`/api/v1/message-feedback-events/all`, { params: query }) as unknown as Promise<FeedbackEventPage>;
}
