import axios from "../request";

// 响应拦截器已解包统一 envelope, 运行时直接返回业务 payload（勿二次 .data 解包）

export type ImportMode = "overwrite" | "append";

export interface PlatformWord {
    id: number;
    word: string;
    source: string;
    create_time: string | null;
}

export interface PlatformWordPage {
    data: PlatformWord[];
    total: number;
}

export interface ImportPreview {
    mode: ImportMode;
    incoming: number;
    added: number;
    removed: number;
    duplicates: number;
    dropped_overlong: number;
    added_sample: string[];
    removed_sample: string[];
}

export interface PlatformPolicy {
    enabled: boolean;
    action: "block" | "mask";
    auto_reply: string;
    auto_reply_is_default: boolean;
}

export function getPlatformWordsApi(params: { keyword?: string; page?: number; page_size?: number } = {}) {
    return axios.get(`/api/v1/platform-sensitive-words`, { params }) as unknown as Promise<PlatformWordPage>;
}

export function addPlatformWordApi(word: string) {
    const form = new FormData();
    form.append("word", word);
    return axios.post(`/api/v1/platform-sensitive-words`, form) as unknown as Promise<{ created: boolean }>;
}

export function deletePlatformWordApi(wordId: number) {
    return axios.delete(`/api/v1/platform-sensitive-words/${wordId}`) as unknown as Promise<{ deleted: boolean }>;
}

export function previewPlatformWordsImportApi(file: File, mode: ImportMode) {
    const form = new FormData();
    form.append("file", file);
    form.append("mode", mode);
    // 覆盖模式下的差异预览是 AC-03 的硬要求：先看数字再确认，避免一个残缺文件静默清空词库
    return axios.post(`/api/v1/platform-sensitive-words/import/preview`, form) as unknown as Promise<ImportPreview>;
}

export function importPlatformWordsApi(file: File, mode: ImportMode) {
    const form = new FormData();
    form.append("file", file);
    form.append("mode", mode);
    return axios.post(`/api/v1/platform-sensitive-words/import`, form) as unknown as Promise<{
        mode: ImportMode;
        written: number;
    }>;
}

export function getPlatformPolicyApi() {
    return axios.get(`/api/v1/platform-sensitive-word-policy`) as unknown as Promise<PlatformPolicy>;
}

export function updatePlatformPolicyApi(data: {
    enabled: boolean;
    action: "block" | "mask";
    auto_reply: string;
}) {
    return axios.put(`/api/v1/platform-sensitive-word-policy`, data) as unknown as Promise<PlatformPolicy>;
}
