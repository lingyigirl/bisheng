import axios from "../request";

/**
 * F058 — document extraction templates, extraction and records.
 *
 * NOTE: the response interceptor already unwraps the unified envelope
 * (`{status_code, status_message, data}` → `data`) for 200 responses. Do NOT
 * `.data` again — see F057 pitfall 3 (`useKnowledgeDepartmentFilter.ts`), where
 * a second unwrap made the whole path silently return `undefined`.
 */

export type DocExtractFieldType = "string" | "number" | "date" | "boolean";

export interface DocExtractFieldDef {
    key: string;
    label: string;
    type: DocExtractFieldType;
    required: boolean;
}

export interface DocExtractTemplate {
    id: number;
    name: string;
    doc_type: string;
    description: string;
    field_schema: DocExtractFieldDef[];
    render_template: string;
    enabled: number;
    create_user?: number | null;
    create_time?: string | null;
    update_time?: string | null;
}

export interface DocExtractTemplatePayload {
    name: string;
    doc_type: string;
    description: string;
    field_schema: DocExtractFieldDef[];
    render_template: string;
}

/** The editor's *unsaved* definition, sent with a trial run (spec AC-41). */
export interface DocExtractTrialDefinition {
    description: string;
    field_schema: DocExtractFieldDef[];
    render_template: string;
}

export interface DocExtractResult {
    ok: boolean;
    fields: Record<string, unknown>;
    markdown: string;
    template: {
        id: number;
        doc_type: string;
        name: string;
        field_schema: DocExtractFieldDef[];
    };
    confidence: number | null;
    route: string | null;
    determined_by: "explicit" | "auto";
    missing_fields: string[];
    error_code?: number | null;
    error_message?: string | null;
    reason?: string | null;
}

export interface DocExtractRun {
    id: number;
    template_id: number;
    doc_type: string;
    determined_by: string;
    confidence: number | null;
    preprocess_route: string | null;
    fields: Record<string, unknown>;
    markdown: string;
    status: number;
    error_code: number | null;
    error_message: string | null;
    user_id: number | null;
    create_time: string | null;
}

export interface DocExtractRecordPage {
    data: DocExtractRun[];
    next_cursor: number | null;
}

// --- templates --------------------------------------------------------------

export function listTemplatesApi(enabledOnly = false) {
    return axios.get(`/api/v1/doc_extract/templates`, {
        params: { enabled_only: enabledOnly },
    }) as unknown as Promise<DocExtractTemplate[]>;
}

export function getTemplateApi(templateId: number) {
    return axios.get(`/api/v1/doc_extract/templates/${templateId}`) as unknown as Promise<DocExtractTemplate>;
}

export function createTemplateApi(data: DocExtractTemplatePayload) {
    return axios.post(`/api/v1/doc_extract/templates`, data) as unknown as Promise<DocExtractTemplate>;
}

export function updateTemplateApi(templateId: number, data: DocExtractTemplatePayload) {
    return axios.put(`/api/v1/doc_extract/templates/${templateId}`, data) as unknown as Promise<DocExtractTemplate>;
}

/** Toggling is its own sub-resource so it does not resend the whole definition. */
export function setTemplateEnabledApi(templateId: number, enabled: boolean) {
    return axios.put(`/api/v1/doc_extract/templates/${templateId}/enabled`, {
        enabled,
    }) as unknown as Promise<DocExtractTemplate>;
}

export function deleteTemplateApi(templateId: number) {
    return axios.delete(`/api/v1/doc_extract/templates/${templateId}`) as unknown as Promise<unknown>;
}

// --- extraction -------------------------------------------------------------

export function classifyDocumentApi(file: File) {
    const form = new FormData();
    form.append("file", file);
    return axios.post(`/api/v1/doc_extract/classify`, form) as unknown as Promise<{
        doc_type: string;
        confidence: number;
        template_id: number;
        route: string;
    }>;
}

export interface ExtractParams {
    file: File;
    docType?: string;
    dryRun?: boolean;
    templateId?: number;
    trialDefinition?: DocExtractTrialDefinition;
}

export function extractDocumentApi(params: ExtractParams) {
    const form = new FormData();
    form.append("file", params.file);
    if (params.docType) form.append("doc_type", params.docType);
    form.append("dry_run", params.dryRun ? "true" : "false");
    if (params.templateId != null) form.append("template_id", String(params.templateId));
    if (params.trialDefinition) {
        form.append("trial_definition", JSON.stringify(params.trialDefinition));
    }
    return axios.post(`/api/v1/doc_extract/extract`, form) as unknown as Promise<DocExtractResult>;
}

// --- records ----------------------------------------------------------------

export interface ListRecordsParams {
    templateId?: number;
    status?: number;
    startTime?: string;
    endTime?: string;
    cursor?: number | null;
    limit?: number;
}

export function listRecordsApi(params: ListRecordsParams = {}) {
    const query: Record<string, unknown> = { limit: params.limit ?? 20 };
    if (params.templateId != null) query.template_id = params.templateId;
    if (params.status != null) query.status = params.status;
    if (params.startTime) query.start_time = params.startTime;
    if (params.endTime) query.end_time = params.endTime;
    if (params.cursor != null) query.cursor = params.cursor;
    return axios.get(`/api/v1/doc_extract/records`, {
        params: query,
    }) as unknown as Promise<DocExtractRecordPage>;
}
