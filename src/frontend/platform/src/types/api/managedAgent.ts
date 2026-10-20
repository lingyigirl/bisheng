/** F078 managed-agent registry DTOs (mirror backend domain/schemas/agent.py). */

export type ManagedAgentSourceKind = "native" | "low_code" | "high_code"
export type ManagedAgentProtocol = "native" | "openai_compatible" | "custom_http"
export type ManagedAgentStatus = "draft" | "online" | "offline" | "disabled"
export type ManagedAgentHealthStatus = "unknown" | "healthy" | "unhealthy"

export interface ManagedAgentCredential {
    auth_type: "none" | "bearer" | "api_key"
    secret?: string
    headers?: Record<string, string>
}

export interface ManagedAgent {
    id: number
    name: string
    description: string | null
    source_kind: ManagedAgentSourceKind
    protocol: ManagedAgentProtocol
    status: ManagedAgentStatus
    version: string | null
    native_app_id: string | null
    native_app_type: number | null
    endpoint: string | null
    model_name: string | null
    scenario_code: string | null
    industry_code: string | null
    scenario_name: string | null
    industry_name: string | null
    tags: number[]
    health_status: ManagedAgentHealthStatus
    health_method: "health_url" | "test_invoke"
    last_health_check_at: string | null
    last_call_at: string | null
    last_run_status: "success" | "failed" | null
    last_latency_ms: number | null
    credential_type: "none" | "bearer" | "api_key"
    has_credential: boolean
    credential_mask: string | null
    create_time: string | null
    update_time: string | null
}

export interface ManagedAgentPage {
    data: ManagedAgent[]
    total: number
}

export interface ManagedAgentCreate {
    name: string
    description?: string
    source_kind: ManagedAgentSourceKind
    protocol: ManagedAgentProtocol
    native_app_id?: string
    native_app_type?: number
    endpoint?: string
    model_name?: string
    agent_card?: Record<string, unknown>
    credential?: ManagedAgentCredential
    health_url?: string
    health_method?: "health_url" | "test_invoke"
    scenario_code?: string
    industry_code?: string
    tags?: number[]
    version?: string
}

export type ManagedAgentUpdate = Partial<ManagedAgentCreate>

export interface ManagedAgentTaxonomy {
    id: number
    kind: "scenario" | "industry"
    code: string
    name_zh: string
    name_en: string | null
    name_ja: string | null
    sort_order: number
    is_enabled: boolean
}

export interface ManagedAgentTaxonomyCreate {
    kind: "scenario" | "industry"
    code: string
    name_zh: string
    name_en?: string
    name_ja?: string
    sort_order?: number
    is_enabled?: boolean
}

export interface ManagedAgentStats {
    calls: number
    success_count: number
    failure_count: number
    success_rate: number
    avg_latency_ms: number | null
}
