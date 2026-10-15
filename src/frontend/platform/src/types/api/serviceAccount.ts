// Types for F076 open-API service accounts (admin console).
// Field names mirror the backend payloads 1:1 (snake_case, no mapping layer).

export interface ServiceAccount {
    id: number
    name: string
    description: string | null
    resource_owner_user_id: number
    enabled: boolean
    // Active (non-revoked) key count, computed by the backend list endpoint.
    key_count: number
    last_used_at: string | null
}

export interface ServiceAccountCreate {
    name: string
    description?: string | null
}

export interface ServiceAccountUpdate {
    name?: string
    description?: string | null
    resource_owner_user_id?: number
}

export interface ServiceAccountPage {
    data: ServiceAccount[]
    total: number
}

export interface OpenApiScope {
    code: string
    group: string
    endpoint_count: number
}
