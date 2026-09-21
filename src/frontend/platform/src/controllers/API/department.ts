import axios from "@/controllers/request"
import {
  DepartmentAdmin,
  DepartmentCreateForm,
  DepartmentDetail,
  DepartmentMember,
  DepartmentSearchResult,
  DepartmentTreeNode,
  DepartmentUpdateForm,
  NodeTypeImpact,
} from "@/types/api/department"

/** dept_id 常含 `BS@...`，必须编码进 URL path，否则部分代理/网关会误解析成 404 */
function depSeg(deptId: string): string {
  return encodeURIComponent(deptId)
}

// ── Lazy tree (F038) ───────────────────────────────────
// Per-layer browse / server-side search / locate-by-id, so large org trees never
// load all nodes at once. `parent_id=null` returns the root layer; nodes carry
// `has_children` for the expand arrow. `include_archived` is true only for the
// management nav tree (AC-16); pickers leave it false (archived not selectable).

/**
 * F057: one layer of the CALLER's own department chain (their departments plus
 * ancestors), for the knowledge page's filter tree.
 *
 * Not `/departments/children`: that family is department MANAGEMENT and F038
 * scopes it to system admins / department admins / tenant admins, answering
 * 21009 for everyone else — so ordinary members, exactly who needs this filter,
 * get a permission error. This one is scoped by membership.
 *
 * Node shape is identical to `getDepartmentChildrenApi`, so the lazy tree
 * component consumes it unchanged.
 */
export async function getMyDepartmentsApi(
  parentId: number | null,
  includeArchived = false
): Promise<DepartmentTreeNode[]> {
  return await axios.get(`/api/v1/knowledge/my_departments`, {
    params: {
      parent_id: parentId ?? undefined,
      include_archived: includeArchived,
    },
  })
}

/**
 * F057: name search over the same tenant-wide department tree.
 *
 * Server-side, not filtered from the loaded layers: the tree is the whole
 * organisation, so a client-side search only sees what the user already
 * expanded and reports "no results" for departments that are plainly there.
 */
export async function searchMyDepartmentsApi(
  keyword: string,
  limit = 50,
  includeArchived = false
): Promise<DepartmentSearchResult> {
  return await axios.get(`/api/v1/knowledge/my_departments/search`, {
    params: { keyword, limit, include_archived: includeArchived },
  })
}

export async function getDepartmentChildrenApi(
  parentId: number | null,
  includeArchived = false
): Promise<DepartmentTreeNode[]> {
  return await axios.get(`/api/v1/departments/children`, {
    params: {
      parent_id: parentId ?? undefined,
      include_archived: includeArchived,
    },
  })
}

export async function searchDepartmentsApi(
  keyword: string,
  includeArchived = false,
  limit = 50
): Promise<DepartmentSearchResult> {
  return await axios.get(`/api/v1/departments/search`, {
    params: { keyword, include_archived: includeArchived, limit },
  })
}

/** Locate/reveal a (possibly deep) department by its internal id → a pruned tree
 *  from the visible root down to it, for echoing a selected value. */
export async function getDepartmentPathTreeApi(
  deptInternalId: number,
  includeArchived = false
): Promise<DepartmentSearchResult> {
  return await axios.get(`/api/v1/departments/${deptInternalId}/path-tree`, {
    params: { include_archived: includeArchived },
  })
}

// ── CRUD ───────────────────────────────────────────────

export async function createDepartmentApi(
  data: DepartmentCreateForm
): Promise<any> {
  return await axios.post(`/api/v1/departments/`, data)
}

export async function getDepartmentApi(
  deptId: string
): Promise<DepartmentDetail> {
  return await axios.get(`/api/v1/departments/${depSeg(deptId)}`)
}

/**
 * F057: the caller's own primary department (empty array when they have none).
 *
 * Note this route takes a numeric `user_id` QUERY param, not a `dept_id` path
 * segment — do not route it through `getDepartmentApi`, whose `depSeg` encoding
 * is for the `BS@...` business key.
 */
export async function getMyPrimaryDepartmentApi(): Promise<
  Array<{ id: number; dept_id: string; name: string }>
> {
  return await axios.get(`/api/v1/departments/get_user_primary_department`)
}

export async function updateDepartmentApi(
  deptId: string,
  data: DepartmentUpdateForm
): Promise<any> {
  return await axios.put(`/api/v1/departments/${depSeg(deptId)}`, data)
}

/**
 * F062: preview how many departments / libraries a marker flip would affect.
 *
 * Read-only, but gated by the SAME permission as the flip — a caller who may not
 * change the marker cannot learn its blast radius. Call this BEFORE submitting a
 * changed `is_department` and show the numbers in a confirm dialog: the value
 * moves at most one bit, but what it changes is who can see whose knowledge.
 *
 * Returns `unavailable: true` when the tenant is too large to evaluate; the
 * dialog must present that as "cannot estimate", never as zero.
 */
export async function getNodeTypeImpactApi(
  deptId: string
): Promise<NodeTypeImpact> {
  return await axios.get(
    `/api/v1/departments/${depSeg(deptId)}/node-type-impact`
  )
}

/**
 * F062: the same preview, for moving a node under a new parent.
 *
 * `newParentId` is the internal numeric PK, matching what `moveDepartmentApi`
 * sends — not the `BS@...` business key used in the path.
 */
export async function getMoveImpactApi(
  deptId: string,
  newParentId: number
): Promise<NodeTypeImpact> {
  return await axios.get(`/api/v1/departments/${depSeg(deptId)}/move-impact`, {
    params: { new_parent_id: newParentId },
  })
}

/**
 * F062 (AC-08): annotate several nodes as 部门 / 机构 in one action.
 *
 * All-or-nothing on the server side: if any node is unauthorised, archived or
 * missing, nothing is written. Callers should NOT batch by looping this per node
 * themselves — a partial apply would express one intention as several different
 * facts about the tree, which is exactly what decides who can read what.
 */
export async function batchSetNodeTypeApi(
  deptIds: string[],
  isDepartment: boolean
): Promise<{ updated: number; requested: number }> {
  return await axios.put(`/api/v1/departments/node-type/batch`, {
    dept_ids: deptIds,
    is_department: isDepartment,
  })
}

export async function deleteDepartmentApi(deptId: string): Promise<any> {
  return await axios.delete(`/api/v1/departments/${depSeg(deptId)}`)
}

export async function purgeDepartmentApi(deptId: string): Promise<any> {
  return await axios.delete(`/api/v1/departments/${depSeg(deptId)}/purge`)
}

export async function restoreDepartmentApi(deptId: string): Promise<any> {
  return await axios.post(`/api/v1/departments/${depSeg(deptId)}/restore`)
}

// ── Mount as Child Tenant ──────────────────────────────
// mount-tenant uses the integer PK (not the BS@... business id) per
// `MountTenantRequest` on the backend; see tenant_mount.py:79.

export interface MountTenantPayload {
  // Optional: backend auto-generates ``t{dept_id}`` when omitted.
  tenant_code?: string
  tenant_name: string
}

export async function mountTenantApi(
  deptIdInt: number,
  data: MountTenantPayload
): Promise<{ id: number; tenant_code: string; tenant_name: string; parent_tenant_id: number; status: string }> {
  return await axios.post(`/api/v1/departments/${deptIdInt}/mount-tenant`, data)
}

/** v2.5.1 收窄到唯一路径：资源迁回 Root + Child 归档。
 *
 * Returns ``migrated_counts`` mapping table → row count moved into Root.
 */
export async function unmountTenantApi(
  deptIdInt: number,
): Promise<{ tenant_id: number; migrated_counts: Record<string, number> }> {
  return await axios.delete(`/api/v1/departments/${deptIdInt}/mount-tenant`)
}

// ── Move ───────────────────────────────────────────────

export async function moveDepartmentApi(
  deptId: string,
  newParentId: number
): Promise<any> {
  return await axios.post(`/api/v1/departments/${depSeg(deptId)}/move`, {
    new_parent_id: newParentId,
  })
}

// ── Members ────────────────────────────────────────────

export async function getDepartmentMembersApi(
  deptId: string,
  params: {
    page: number
    limit: number
    keyword?: string
    is_primary?: number
  }
): Promise<{ data: DepartmentMember[]; total: number }> {
  return await axios.get(`/api/v1/departments/${depSeg(deptId)}/members`, { params })
}

/** 全组织成员搜索（主属部门路径），可见范围与部门树一致 */
export interface GlobalMemberSearchRow {
  user_id: number
  user_name: string
  external_id?: string | null
  primary_department_dept_id: string
  primary_department_path: string
  /** false = 账号未启用（User.delete=1），与部门成员列表一致 */
  enabled?: boolean
}

export async function searchGlobalMembersApi(params: {
  keyword: string
  page?: number
  limit?: number
  rootDeptId?: number
}, config?: { signal?: AbortSignal }): Promise<{ data: GlobalMemberSearchRow[]; total: number }> {
  return await axios.get(`/api/v1/departments/search/global-members`, {
    params: {
      keyword: params.keyword,
      page: params.page ?? 1,
      limit: params.limit ?? 20,
      root_dept_id: params.rootDeptId,
    },
    signal: config?.signal,
  })
}

export async function addDepartmentMembersApi(
  deptId: string,
  data: { user_ids: number[]; is_primary: number }
): Promise<any> {
  return await axios.post(`/api/v1/departments/${depSeg(deptId)}/members`, data)
}

export async function removeDepartmentMemberApi(
  deptId: string,
  userId: number
): Promise<any> {
  return await axios.delete(`/api/v1/departments/${depSeg(deptId)}/members/${userId}`)
}

export type DepartmentMemberEditMode = "affiliate" | "local_primary" | "synced_primary"

export type DepartmentMemberEditForm = {
  edit_mode: DepartmentMemberEditMode
  user: {
    user_id: number
    user_name: string
    person_id: string
    source: string
  }
  context: { dept_id: string; name: string; is_primary: number }
  primary_department: null | {
    id: number
    dept_id: string
    name: string
    role_ids: number[]
  }
  can_change_primary: boolean
  affiliate_rows: { dept_id: string; name: string; role_ids: number[] }[]
  assignable_roles_catalog: Record<
    string,
    { id: number; role_name: string; role_type?: string; department_id?: number | null }[]
  >
  context_role_ids: number[]
  manageable_groups: { id: number; group_name: string; visibility?: string }[]
  current_group_ids: number[]
  locked_group_ids: number[]
}

export async function getDepartmentMemberEditFormApi(
  deptId: string,
  userId: number
): Promise<DepartmentMemberEditForm> {
  return await axios.get(
    `/api/v1/departments/${depSeg(deptId)}/members/${userId}/edit-form`
  )
}

export async function applyDepartmentMemberEditApi(
  deptId: string,
  userId: number,
  body: {
    user_name?: string | null
    primary_department_id?: number | null
    group_ids?: number[] | null
    context_role_ids?: number[] | null
    primary_role_ids?: number[] | null
    affiliate_roles?: { dept_id: string; role_ids: number[] }[] | null
  }
): Promise<void> {
  await axios.post(
    `/api/v1/departments/${depSeg(deptId)}/members/${userId}/apply-edit`,
    body
  )
}

export async function checkDepartmentMemberDeleteApi(
  deptId: string,
  userId: number
): Promise<{
  has_assets: boolean
  counts: { knowledge_spaces: number; flows: number; assistants: number }
}> {
  return await axios.get(
    `/api/v1/departments/${depSeg(deptId)}/members/${userId}/delete-check`
  )
}

export async function deleteDepartmentLocalMemberApi(
  deptId: string,
  userId: number
): Promise<void> {
  await axios.delete(
    `/api/v1/departments/${depSeg(deptId)}/members/${userId}/local-account`
  )
}

export async function getDepartmentAssignableRolesApi(
  deptId: string
): Promise<{ id: number; role_name: string; role_type: string; department_id?: number | null }[]> {
  return await axios.get(`/api/v1/departments/${depSeg(deptId)}/assignable-roles`)
}

export async function createDepartmentLocalMemberApi(
  deptId: string,
  data: { user_name: string; person_id: string; password: string; role_ids: number[] }
): Promise<{ user_id: number; user_name: string; person_id: string; dept_id: string }> {
  // dept_id 放 body，避免路径含 BS@ 时部分网关/反代误解析为 404（与 POST .../{dept_id}/local-members 等价）
  return await axios.post(`/api/v1/departments/local-members`, {
    dept_id: deptId,
    ...data,
  })
}

// ── Admins ─────────────────────────────────────────────

export async function getDepartmentAdminsApi(
  deptId: string
): Promise<DepartmentAdmin[]> {
  return await axios.get(`/api/v1/departments/${depSeg(deptId)}/admins`)
}

export async function setDepartmentAdminsApi(
  deptId: string,
  userIds: number[]
): Promise<DepartmentAdmin[]> {
  return await axios.put(`/api/v1/departments/${depSeg(deptId)}/admins`, {
    user_ids: userIds,
  })
}
