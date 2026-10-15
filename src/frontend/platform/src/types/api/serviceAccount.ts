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

export interface OpenApiScopePage {
    data: OpenApiScope[]
    total: number
}

/** Derived by the backend at read time; `expires_at` wins only when not revoked. */
export type ServiceAccountKeyStatus = "active" | "revoked" | "expired"

export interface ServiceAccountKey {
    id: number
    name: string | null
    /** Always `bs-sak-********XXXX` -- the plaintext is never retrievable again. */
    key_mask: string
    scopes: string[]
    /** null = never expires. */
    expires_at: string | null
    revoked_at: string | null
    last_used_at: string | null
    status: ServiceAccountKeyStatus
}

export interface ServiceAccountKeyPage {
    data: ServiceAccountKey[]
    total: number
}

export interface ServiceAccountKeyIssue {
    name?: string | null
    /** Must come from the scope catalog endpoint. */
    scopes: string[]
    /** Naive local ISO string; omit or null = never expires. */
    expires_at?: string | null
}

/**
 * Issue response. `plaintext` exists only here: it is never stored, logged or
 * returned by any other endpoint, so the console must reveal it once and drop it.
 */
export interface ServiceAccountKeyIssued {
    id: number
    key_mask: string
    plaintext: string
    scopes: string[]
    expires_at: string | null
    create_time: string | null
}

/** PATCH semantics: send only the fields that actually changed. */
export interface ServiceAccountKeyUpdate {
    name?: string | null
    /** Never send null -- the column is NOT NULL. */
    scopes?: string[]
    /** Explicit null clears the expiry (= never expires). */
    expires_at?: string | null
}
