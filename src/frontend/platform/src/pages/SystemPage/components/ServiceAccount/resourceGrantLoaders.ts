// F076: shared resource loaders for the service-account grant UI.
// Kept out of ResourceGrantsTab.tsx so both the table and the add-grant dialog
// can use them without pushing either file past the 600-line limit.

import { getAppsApi } from "@/controllers/API/flow"
import { readFileLibDatabase } from "@/controllers/API/index"
import { listManagedAgentsApi } from "@/controllers/API/managedAgent"
import { ResourceGrantLevel, ResourceGrantType } from "@/types/api/serviceAccount"

/** Backend registry order (resource_grant_service.GRANTABLE_RESOURCE_TYPES). */
export const RESOURCE_TYPES: ResourceGrantType[] = [
    "knowledge_library",
    "knowledge_space",
    "assistant",
    "workflow",
    "managed_agent",
]
export const GRANT_LEVELS: ResourceGrantLevel[] = ["viewer", "editor", "manager"]

export interface PickerItem {
    id: string
    name: string
}

/**
 * Where the grant dialog searches for candidates. The backend re-checks every id
 * (unsupported type / level → 400, no manager rights on the resource → 403), so
 * these lists only need to be convenient, not authoritative.
 */
export const RESOURCE_LOADERS: Record<ResourceGrantType, (keyword: string, pageSize: number) => Promise<PickerItem[]>> =
    {
        // managed=true asks the backend for apps the caller may edit; admins bypass it.
        assistant: async (keyword, pageSize) =>
            (await getAppsApi({ type: "assistant", keyword, pageSize, managed: true })).data.map((one) => ({
                id: String(one.id),
                name: one.name,
            })),
        workflow: async (keyword, pageSize) =>
            (await getAppsApi({ type: "flow", keyword, pageSize, managed: true })).data.map((one) => ({
                id: String(one.id),
                name: one.name,
            })),
        knowledge_library: async (keyword, pageSize) => {
            // Document bases (type=0) and QA bases (type=1) are the same FGA plane; asking
            // for type=0 alone would make every QA base unofferable here.
            const [docs, qas] = await Promise.all([
                readFileLibDatabase({ name: keyword, type: 0, pageSize }),
                readFileLibDatabase({ name: keyword, type: 1, pageSize }),
            ])
            return [...docs.data, ...qas.data].map((one) => ({ id: String(one.id), name: one.name }))
        },
        // Spaces live in the same table (type=3).
        knowledge_space: async (keyword, pageSize) =>
            (await readFileLibDatabase({ name: keyword, type: 3, pageSize })).data.map((one) => ({
                id: String(one.id),
                name: one.name,
            })),
        // F078: only agents visible to the caller are offered; the backend re-checks
        // the grant with PermissionService (11808/11820 on failure).
        managed_agent: async (keyword, pageSize) =>
            (await listManagedAgentsApi({ keyword, page_size: pageSize })).data.map((one) => ({
                id: String(one.id),
                name: one.name,
            })),
    }

/**
 * Picker page size. 100 is the strictest backend ceiling among the five loaders
 * (`/api/v1/managed-agents` declares `page_size le=100`); the other list APIs
 * accept it too. `knowledge_library` merges two calls, so it can show up to 200.
 */
export const PICKER_PAGE_SIZE = 100

/**
 * Ids whose names are resolved eagerly for the table; beyond this they show as
 * raw ids. Capped for the same managed-agent `page_size le=100` reason (one
 * call per type; knowledge_library makes two, so up to 200 names).
 */
export const NAME_LOOKUP_PAGE_SIZE = 100
