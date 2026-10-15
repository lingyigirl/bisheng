// F076 open-API service accounts (admin console).
// The request wrapper already unwraps the response envelope: these functions
// resolve to the envelope `data` payload directly (see @/controllers/request).
import axios from "@/controllers/request"
import {
    ServiceAccount,
    ServiceAccountCreate,
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
