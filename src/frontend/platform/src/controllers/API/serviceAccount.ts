// F076 open-API service accounts (admin console).
// The request wrapper already unwraps the response envelope: these functions
// resolve to the envelope `data` payload directly (see @/controllers/request).
import axios from "@/controllers/request"
import {
    AssignableAgentPage,
    OpenApiBaseUrl,
    OpenApiScopePage,
    ResourceGrantItemInput,
    ResourceGrantMutateResult,
    ResourceGrantPage,
    ResourceGrantRevokeInput,
    ServiceAccount,
    ServiceAccountCreate,
    ServiceAccountKey,
    ServiceAccountKeyIssue,
    ServiceAccountKeyIssued,
    ServiceAccountKeyPage,
    ServiceAccountKeyRevealed,
    ServiceAccountKeyUpdate,
    ServiceAccountPage,
    ServiceAccountUpdate,
} from "@/types/api/serviceAccount"

export async function listServiceAccountsApi(
    params: { keyword?: string; page?: number; page_size?: number } = {},
): Promise<ServiceAccountPage> {
    return await axios.get(`/api/v1/service-accounts`, {
        params: { page: 1, page_size: 20, ...params },
    })
}

export async function createServiceAccountApi(data: ServiceAccountCreate): Promise<ServiceAccount> {
    return await axios.post(`/api/v1/service-accounts`, data)
}

/**
 * Single account with live stats. PATCH / enable / disable responses carry a
 * stale key_count(0) and last_used_at(null), so refresh the detail view from
 * this endpoint after any of those writes.
 */
export async function getServiceAccountApi(accountId: number): Promise<ServiceAccount> {
    return await axios.get(`/api/v1/service-accounts/${accountId}`)
}

/** Returns every key of the account (revoked ones included); the endpoint takes no page params. */
export async function listServiceAccountKeysApi(accountId: number): Promise<ServiceAccountKeyPage> {
    return await axios.get(`/api/v1/service-accounts/${accountId}/keys`)
}

/** `data.plaintext` is returned exactly once -- hand it straight to the reveal dialog. */
export async function issueServiceAccountKeyApi(
    accountId: number,
    data: ServiceAccountKeyIssue,
): Promise<ServiceAccountKeyIssued> {
    return await axios.post(`/api/v1/service-accounts/${accountId}/keys`, data)
}

export async function updateServiceAccountKeyApi(
    accountId: number,
    keyId: number,
    data: ServiceAccountKeyUpdate,
): Promise<ServiceAccountKey> {
    return await axios.patch(`/api/v1/service-accounts/${accountId}/keys/${keyId}`, data)
}

export async function revokeServiceAccountKeyApi(
    accountId: number,
    keyId: number,
): Promise<{ id: number; revoked: boolean }> {
    return await axios.post(`/api/v1/service-accounts/${accountId}/keys/${keyId}/revoke`)
}

/**
 * Explicit admin reveal. Every call writes an audit row; the response must not
 * be cached or persisted anywhere outside the dialog that shows it.
 */
export async function revealServiceAccountKeyApi(
    accountId: number,
    keyId: number,
): Promise<ServiceAccountKeyRevealed> {
    return await axios.post(`/api/v1/service-accounts/${accountId}/keys/${keyId}/reveal`)
}

export async function revokeAllServiceAccountKeysApi(
    accountId: number,
): Promise<{ account_id: number; revoked: number }> {
    return await axios.post(`/api/v1/service-accounts/${accountId}/keys/revoke-all`)
}

/** Catalog of scopes a key may be issued with; order is the backend registry order. */
export async function listOpenApiScopesApi(): Promise<OpenApiScopePage> {
    return await axios.get(`/api/v1/open-api/scopes`)
}

/**
 * What this account can currently see, and how each entry was obtained.
 * Not paginated; the subject is always the account's dedicated identity.
 */
export async function listResourceGrantsApi(accountId: number): Promise<ResourceGrantPage> {
    return await axios.get(`/api/v1/service-accounts/${accountId}/resource-grants`)
}

/** Agents the account may be restricted to; the key dialog's picker source. */
export async function listAssignableAgentsApi(accountId: number): Promise<AssignableAgentPage> {
    return await axios.get(`/api/v1/service-accounts/${accountId}/assignable-agents`)
}

/**
 * Grant and/or revoke in one call. Revoking only works on `direct` entries --
 * revoking an inherited visibility answers 404 (nothing to revoke).
 */
export async function mutateResourceGrantsApi(
    accountId: number,
    data: { grants?: ResourceGrantItemInput[]; revokes?: ResourceGrantRevokeInput[] },
): Promise<ResourceGrantMutateResult> {
    return await axios.post(`/api/v1/service-accounts/${accountId}/resource-grants:mutate`, {
        grants: [],
        revokes: [],
        ...data,
    })
}

export async function updateServiceAccountApi(
    accountId: number,
    data: ServiceAccountUpdate,
): Promise<ServiceAccount> {
    return await axios.patch(`/api/v1/service-accounts/${accountId}`, data)
}

export async function deleteServiceAccountApi(accountId: number): Promise<{ id: number; deleted: boolean }> {
    return await axios.delete(`/api/v1/service-accounts/${accountId}`)
}

export async function enableServiceAccountApi(accountId: number): Promise<ServiceAccount> {
    return await axios.post(`/api/v1/service-accounts/${accountId}/enable`)
}

export async function disableServiceAccountApi(accountId: number): Promise<ServiceAccount> {
    return await axios.post(`/api/v1/service-accounts/${accountId}/disable`)
}

/**
 * Where the access guide's default address should come from. `configured` is
 * the ops-set public base URL; `detected` is the backend's guessed host:port and
 * may be null when it cannot determine one.
 *
 * `silent: true`: during a rolling upgrade an older backend answers 404, which
 * the global interceptor would turn into a full-page /404 redirect. The caller
 * treats any failure as "no address sources" and keeps the console default.
 */
export async function getOpenApiBaseUrlApi(): Promise<OpenApiBaseUrl> {
    // eslint-disable-next-line @typescript-eslint/no-explicit-any -- `silent` is a custom axios config flag (see request.ts)
    return await axios.get(`/api/v1/open-api/base-url`, { silent: true } as any)
}
