// F078 managed-agent registry (admin console).
// The request wrapper unwraps the response envelope: these functions resolve to
// the envelope `data` payload directly (see @/controllers/request).
import axios from "@/controllers/request"
import {
    ManagedAgent,
    ManagedAgentCreate,
    ManagedAgentPage,
    ManagedAgentStats,
    ManagedAgentTaxonomy,
    ManagedAgentTaxonomyCreate,
    ManagedAgentUpdate,
} from "@/types/api/managedAgent"

export interface ManagedAgentPickerItem {
    id: number
    name: string
}

export async function listManagedAgentsApi(params: {
    keyword?: string
    source_kind?: string
    protocol?: string
    status?: string
    scenario_code?: string
    industry_code?: string
    page?: number
    page_size?: number
} = {}): Promise<ManagedAgentPage> {
    return await axios.get(`/api/v1/managed-agents`, {
        params: { page: 1, page_size: 20, ...params },
    })
}

export async function getManagedAgentApi(agentId: number): Promise<ManagedAgent> {
    return await axios.get(`/api/v1/managed-agents/${agentId}`)
}

export async function createManagedAgentApi(data: ManagedAgentCreate): Promise<ManagedAgent> {
    return await axios.post(`/api/v1/managed-agents`, data)
}

export async function updateManagedAgentApi(agentId: number, data: ManagedAgentUpdate): Promise<ManagedAgent> {
    return await axios.put(`/api/v1/managed-agents/${agentId}`, data)
}

export async function deleteManagedAgentApi(agentId: number): Promise<void> {
    return await axios.delete(`/api/v1/managed-agents/${agentId}`)
}

export async function transitionManagedAgentApi(
    agentId: number,
    action: "publish" | "offline" | "disable",
): Promise<ManagedAgent> {
    return await axios.post(`/api/v1/managed-agents/${agentId}/${action}`)
}

export async function healthCheckManagedAgentApi(agentId: number): Promise<ManagedAgent> {
    return await axios.post(`/api/v1/managed-agents/${agentId}/health-check`)
}

export async function managedAgentStatsApi(
    agentId: number,
    params: { start_time?: string; end_time?: string } = {},
): Promise<ManagedAgentStats> {
    return await axios.get(`/api/v1/managed-agents/${agentId}/stats`, { params })
}

export async function listTaxonomiesApi(params: {
    kind?: "scenario" | "industry"
    enabled_only?: boolean
} = {}): Promise<ManagedAgentTaxonomy[]> {
    return await axios.get(`/api/v1/managed-agent-taxonomies`, { params })
}

export async function createTaxonomyApi(data: ManagedAgentTaxonomyCreate): Promise<ManagedAgentTaxonomy> {
    return await axios.post(`/api/v1/managed-agent-taxonomies`, data)
}

export async function deleteTaxonomyApi(rowId: number): Promise<void> {
    return await axios.delete(`/api/v1/managed-agent-taxonomies/${rowId}`)
}
