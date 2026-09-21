import axios from "../request";

// F060 术语库。响应拦截器已解包统一 envelope, 运行时直接返回业务 payload（勿二次 .data 解包）

export type TermImportMode = "overwrite" | "append";

export interface Term {
    id: number;
    standard_term: string;
    aliases: string[];
    definition: string;
    department_id: number | null;
    create_user_id: number | null;
    create_time: string | null;
    update_time: string | null;
}

export interface TermPage {
    data: Term[];
    total: number;
    page: number;
    page_size: number;
}

/**
 * What an import would change (spec AC-07).
 *
 * Counts, not name lists: a term is a name plus a definition plus a set of
 * aliases, so a sample of them would not fit the dialog. `removed` is what makes
 * the overwrite warning concrete — "this replaces everything" is a warning the
 * operator dismisses, "this removes 213 terms" is one they read (spec AC-08).
 */
export interface TermImportPreview {
    mode: TermImportMode;
    added: number;
    duplicates: number;
    dropped_overlong: number;
    removed: number;
}

export interface TermPayload {
    standard_term: string;
    definition: string;
    aliases: string[];
    department_id: number | null;
}

export function getTermsApi(params: {
    department_id?: number;
    include_sub_departments?: boolean;
    page?: number;
    page_size?: number;
} = {}) {
    return axios.get(`/api/v1/term-library`, { params }) as unknown as Promise<TermPage>;
}

export function createTermApi(data: TermPayload) {
    return axios.post(`/api/v1/term-library`, data) as unknown as Promise<Term>;
}

export function updateTermApi(termId: number, data: TermPayload) {
    return axios.put(`/api/v1/term-library/${termId}`, data) as unknown as Promise<Term>;
}

export function deleteTermApi(termId: number) {
    return axios.delete(`/api/v1/term-library/${termId}`) as unknown as Promise<{ deleted: boolean }>;
}

function importForm(file: File, mode: TermImportMode) {
    const form = new FormData();
    form.append("file", file);
    form.append("mode", mode);
    return form;
}

/**
 * The counts the dialog shows before anything is written (spec AC-07 / AC-09).
 *
 * A file the backend cannot read fails HERE rather than after an overwrite has
 * been confirmed — the error code (11505) is what the user sees.
 */
export function previewTermsImportApi(file: File, mode: TermImportMode) {
    return axios.post(`/api/v1/term-library/import/preview`, importForm(file, mode)) as unknown as Promise<TermImportPreview>;
}

export function importTermsApi(file: File, mode: TermImportMode) {
    return axios.post(`/api/v1/term-library/import`, importForm(file, mode)) as unknown as Promise<TermImportPreview>;
}
