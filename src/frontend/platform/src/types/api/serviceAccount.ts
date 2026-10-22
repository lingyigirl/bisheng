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

/** Resource kinds the console may delegate; mirrors backend GRANTABLE_RESOURCE_TYPES. */
export type ResourceGrantType =
    | "knowledge_library"
    | "knowledge_space"
    | "assistant"
    | "workflow"
    | "managed_agent"

export type ResourceGrantLevel = "viewer" | "editor" | "manager"

/**
 * `direct` = granted through this console (revocable here);
 * `inherited` = visibility coming from a department / tenant path, which the
 * console neither granted nor can revoke.
 */
export type ResourceGrantSource = "direct" | "inherited"

export interface ResourceGrant {
    resource_type: ResourceGrantType
    resource_id: string
    /** null = no delegable level (e.g. the identity owns the resource). */
    level: ResourceGrantLevel | null
    source: ResourceGrantSource
}

export interface ResourceGrantPage {
    data: ResourceGrant[]
    total: number
}

export interface ResourceGrantItemInput {
    resource_type: ResourceGrantType
    resource_id: string
    level: ResourceGrantLevel
}

export interface ResourceGrantRevokeInput {
    resource_type: ResourceGrantType
    resource_id: string
}

export interface ResourceGrantMutateResult {
    account_id: number
    /** Request entry counts, not the number of tuples written. */
    granted: number
    revoked: number
}

export interface OpenApiScopePage {
    data: OpenApiScope[]
    total: number
}

/** Agent object types a per-key allowlist may contain. */
export type AgentType = "assistant" | "workflow" | "managed_agent"

/** One entry of a key's agent allowlist (backend `allowed_apps`). */
export interface AgentRef {
    type: AgentType
    id: string
}

/**
 * The account's granted agents, used by the key dialog picker.
 * `invocable` is advisory: false means the current grant level allows reading
 * but not invoking (e.g. a viewer-level workflow).
 */
export interface AssignableAgent extends AgentRef {
    name: string
    level: ResourceGrantLevel | null
    invocable: boolean
}

export interface AssignableAgentPage {
    data: AssignableAgent[]
    total: number
}

/** Derived by the backend at read time; `expires_at` wins only when not revoked. */
export type ServiceAccountKeyStatus = "active" | "revoked" | "expired"

export interface ServiceAccountKey {
    id: number
    name: string | null
    /** Mask only (`bs-sak-********XXXX`); use the reveal endpoint for plaintext. */
    key_mask: string
    scopes: string[]
    /** null = unrestricted; [] = no agent may be called. */
    allowed_apps: AgentRef[] | null
    /** False for keys issued before the reveal feature existed. */
    revealable: boolean
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
    /** null/omitted = unrestricted; [] = no agent may be called. */
    allowed_apps?: AgentRef[] | null
    /** Naive local ISO string; omit or null = never expires. */
    expires_at?: string | null
}

/** Issue response. `plaintext` is also retrievable later via the reveal action. */
export interface ServiceAccountKeyIssued {
    id: number
    key_mask: string
    plaintext: string
    scopes: string[]
    allowed_apps: AgentRef[] | null
    expires_at: string | null
    create_time: string | null
}

/** Explicit admin reveal; the backend writes an audit row for every call. */
export interface ServiceAccountKeyRevealed {
    id: number
    key_mask: string
    plaintext: string
    scopes: string[]
    allowed_apps: AgentRef[] | null
    expires_at: string | null
}

/** PATCH semantics: send only the fields that actually changed. */
export interface ServiceAccountKeyUpdate {
    name?: string | null
    /** Never send null -- the column is NOT NULL. */
    scopes?: string[]
    /** Explicit null = unrestricted (legacy behaviour). */
    allowed_apps?: AgentRef[] | null
    /** Explicit null clears the expiry (= never expires). */
    expires_at?: string | null
}

/**
 * Address sources for the access guide. `configured` is the ops-set
 * `open_api.public_base_url` (authoritative); `detected` is the backend's
 * best-effort `http://<host-ip>:<listen-port>` and may be unreachable behind a
 * gateway, so it never overrides `configured`.
 */
export interface OpenApiBaseUrl {
    configured: string
    detected: string | null
}
