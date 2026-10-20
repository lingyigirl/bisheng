// F078 client catalog. The client request wrapper returns the full envelope
// (unlike platform), so callers read `res.data.data` / `res.data.total`.
import request from "./request"

export interface ManagedAgentCatalogItem {
    id: number
    name: string
    description: string | null
    source_kind: "native" | "low_code" | "high_code"
    protocol: string
    status: string
    scenario_code: string | null
    industry_code: string | null
    scenario_name: string | null
    industry_name: string | null
    health_status: string
    last_call_at: string | null
}

export interface ManagedAgentTaxonomyItem {
    id: number
    kind: "scenario" | "industry"
    code: string
    name_zh: string
    name_en: string | null
    name_ja: string | null
}

interface Envelope<T> {
    status_code: number
    status_message: string
    data: T
}

export async function getManagedAgentCatalogApi(params: {
    keyword?: string
    scenario_code?: string
    industry_code?: string
    page?: number
    page_size?: number
}): Promise<Envelope<{ data: ManagedAgentCatalogItem[]; total: number }>> {
    return await request.get("/api/v1/managed-agents/catalog", {
        params: { page: 1, page_size: 20, ...params },
        // A permission failure should not redirect the whole page.
        skip403Redirect: true,
    })
}

export async function getManagedAgentTaxonomiesApi(): Promise<Envelope<ManagedAgentTaxonomyItem[]>> {
    return await request.get("/api/v1/managed-agent-taxonomies", {
        params: { enabled_only: true },
        skip403Redirect: true,
    })
}
