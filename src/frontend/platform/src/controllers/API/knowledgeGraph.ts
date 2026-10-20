import axios from "@/controllers/request"

export interface KnowledgeGraphJobInfo {
  job_id: number
  status: number
  total_files: number
  done_files: number
  failed_files: number
  total_chunks: number
  done_chunks: number
  token_in: number
  token_out: number
  error_message?: string | null
}

export interface KnowledgeGraphStatus {
  state: number
  graph_built_at?: string | null
  stale: boolean
  job?: KnowledgeGraphJobInfo | null
}

export interface KnowledgeGraphOverview {
  state: number
  built_at?: string | null
  entity_count: number
  relation_count: number
  stale: boolean
}

export interface KnowledgeGraphNode {
  id: number
  name: string
  type: string
  description?: string | null
  attributes?: Record<string, unknown> | null
  mention_count: number
  degree: number
}

export interface KnowledgeGraphEdge {
  id: number
  source: number
  target: number
  label: string
  weight: number
  description?: string | null
}

export interface KnowledgeGraphData {
  nodes: KnowledgeGraphNode[]
  edges: KnowledgeGraphEdge[]
  truncated: boolean
}

export interface KnowledgeGraphEntityItem {
  id: number
  name: string
  type: string
  description?: string | null
  attributes?: Record<string, unknown> | null
  mention_count: number
}

export interface KnowledgeGraphEntityList {
  data: KnowledgeGraphEntityItem[]
  total: number
}

export interface KnowledgeGraphRelationItem {
  id: number
  direction: "out" | "in"
  label: string
  other_entity: KnowledgeGraphEntityItem
  weight: number
  description?: string | null
}

export interface KnowledgeGraphEvidenceItem {
  document_id: number
  document_name?: string | null
  chunk_index: number
  snippet?: string | null
}

export interface KnowledgeGraphEntityDetail {
  entity: KnowledgeGraphEntityItem
  relations: KnowledgeGraphRelationItem[]
  evidence: KnowledgeGraphEvidenceItem[]
}

export interface KnowledgeGraphBuildPayload {
  model_id?: number | null
  entity_types?: string[] | null
  force: boolean
}

export async function buildKnowledgeGraphApi(
  knowledgeId: number,
  payload: KnowledgeGraphBuildPayload,
): Promise<{ job_id: number; status: number; total_files: number }> {
  return await axios.post(`/api/v1/knowledge/${knowledgeId}/graph/build`, payload)
}

export async function getKnowledgeGraphStatusApi(knowledgeId: number): Promise<KnowledgeGraphStatus> {
  return await axios.get(`/api/v1/knowledge/${knowledgeId}/graph/status`)
}

export async function cancelKnowledgeGraphApi(
  knowledgeId: number,
): Promise<{ job_id: number; status: number }> {
  return await axios.post(`/api/v1/knowledge/${knowledgeId}/graph/cancel`)
}

export async function getKnowledgeGraphOverviewApi(knowledgeId: number): Promise<KnowledgeGraphOverview> {
  return await axios.get(`/api/v1/knowledge/${knowledgeId}/graph/overview`)
}

export async function getKnowledgeGraphDataApi(
  knowledgeId: number,
  params?: { limit?: number; entity_types?: string },
): Promise<KnowledgeGraphData> {
  return await axios.get(`/api/v1/knowledge/${knowledgeId}/graph/graph`, { params })
}

export async function searchKnowledgeGraphEntitiesApi(
  knowledgeId: number,
  params: { keyword?: string; entity_type?: string; page?: number; page_size?: number },
): Promise<KnowledgeGraphEntityList> {
  return await axios.get(`/api/v1/knowledge/${knowledgeId}/graph/entities`, { params })
}

export async function getKnowledgeGraphEntityApi(
  knowledgeId: number,
  entityId: number,
): Promise<KnowledgeGraphEntityDetail> {
  return await axios.get(`/api/v1/knowledge/${knowledgeId}/graph/entities/${entityId}`)
}

export async function getKnowledgeGraphNeighborsApi(
  knowledgeId: number,
  entityId: number,
  limit = 100,
): Promise<KnowledgeGraphData> {
  return await axios.get(`/api/v1/knowledge/${knowledgeId}/graph/entities/${entityId}/neighbors`, {
    params: { limit },
  })
}

export async function clearKnowledgeGraphApi(knowledgeId: number): Promise<null> {
  return await axios.delete(`/api/v1/knowledge/${knowledgeId}/graph`)
}
