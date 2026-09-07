import axios from "../request";

export const KNOWLEDGE_SPACE_FILE_PARSE_POLICY = "knowledge_space_file_parse";
export const CHANNEL_ARTICLE_POLICY = "channel_article";
export const CHAT_INPUT_POLICY = "chat_input";
export const CHAT_OUTPUT_POLICY = "chat_output";

export type SensitiveWordType = "builtin" | "custom";

export interface SensitiveWordPolicy {
    tenant_id: number;
    business_type: string;
    scope_type: string;
    scope_id: string;
    enabled: boolean;
    words_types: SensitiveWordType[];
    custom_words: string;
    auto_reply: string;
    extra_config: Record<string, unknown>;
}

export interface SensitiveWordPolicyPayload {
    enabled: boolean;
    words_types: SensitiveWordType[];
    custom_words: string;
    auto_reply?: string;
    extra_config?: Record<string, unknown>;
}

// 响应拦截器已解包统一 envelope, 运行时直接返回业务 payload（勿二次 .data 解包）
export function getSensitiveWordPolicyApi(businessType = KNOWLEDGE_SPACE_FILE_PARSE_POLICY) {
    return axios.get(`/api/v1/sensitive-word-policies/${businessType}`) as unknown as Promise<
        SensitiveWordPolicy | null
    >;
}

export function updateSensitiveWordPolicyApi(
    data: SensitiveWordPolicyPayload,
    businessType = KNOWLEDGE_SPACE_FILE_PARSE_POLICY,
) {
    return axios.put(`/api/v1/sensitive-word-policies/${businessType}`, data) as unknown as Promise<
        SensitiveWordPolicy | null
    >;
}
