// F076 open-API service accounts (admin console).
// The request wrapper already unwraps the response envelope: these functions
// resolve to the envelope `data` payload directly (see @/controllers/request).
import axios from "@/controllers/request"
import {
    OpenApiScopePage,
    ServiceAccount,
    ServiceAccountCreate,
    ServiceAccountKey,
    ServiceAccountKeyIssue,
    ServiceAccountKeyIssued,
    ServiceAccountKeyPage,
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

export async function revokeAllServiceAccountKeysApi(
    accountId: number,
): Promise<{ account_id: number; revoked: number }> {
    return await axios.post(`/api/v1/service-accounts/${accountId}/keys/revoke-all`)
}

/** Catalog of scopes a key may be issued with; order is the backend registry order. */
export async function listOpenApiScopesApi(): Promise<OpenApiScopePage> {
    return await axios.get(`/api/v1/open-api/scopes`)
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
