export interface DepartmentTreeNode {
  id: number
  dept_id: string
  name: string
  parent_id: number | null
  path: string
  sort_order: number
  source: string
  status: string
  // F027 AC-14: `member_count` removed from the tree node response. The
  // single-department detail (DepartmentDetail) still carries it (AC-15).
  /** True iff the department is currently a Child Tenant mount point. */
  is_tenant_root: boolean
  /** Tenant.id this department mounts; null when is_tenant_root is false. */
  mounted_tenant_id: number | null
  /**
   * F062: `true` = 部门 (transparent to its ancestors, so departments within the
   * same org domain see each other); `false` = 机构 (opaque — the visibility
   * barrier). Backend encodes it as 0/1 and exposes a bool here.
   */
  is_department?: boolean
  // F038 lazy tree: has_children drives the expand affordance without loading the
  // child layer; matched flags a search/locate hit. Both optional so legacy
  // full-tree consumers are unaffected; the lazy endpoints always populate them.
  has_children?: boolean
  matched?: boolean
  children: DepartmentTreeNode[]
}

/** F038 search / path-tree (locate) response: a pruned tree of hits + ancestors. */
export interface DepartmentSearchResult {
  roots: DepartmentTreeNode[]
  total_matches: number
  truncated: boolean
}

export interface DepartmentAdmin {
  user_id: number
  user_name: string
}

export interface DepartmentDetail {
  id: number
  dept_id: string
  name: string
  parent_id: number | null
  path: string
  sort_order: number
  source: string
  status: string
  default_role_ids: number[] | null
  member_count: number
  /** F062 节点标记：true=部门（对上级透明），false=机构（可见性隔离边界）。 */
  is_department?: boolean
}

export interface DepartmentMember {
  user_id: number
  user_name: string
  /** 本地人员 ID（external_id），用于重名展示；非展示字段 */
  person_id?: string | null
  department_id: number
  is_primary: number
  source: string
  create_time: string
  update_time?: string
  enabled: boolean
  /** Non-empty when the account was disabled by an SSO/HR sync; only super admin may re-enable. */
  disable_source?: string | null
  user_groups: { id: number; group_name: string }[]
  roles: { id: number; role_name: string }[]
  /** 当前部门 OpenFGA admin；用于在角色列最前展示「部门管理员」 */
  is_department_admin?: boolean
}

export interface DepartmentCreateForm {
  name: string
  parent_id: number
  sort_order?: number
  default_role_ids?: number[]
  admin_user_ids?: number[]
}

export interface DepartmentUpdateForm {
  name?: string
  sort_order?: number
  default_role_ids?: number[]
  /** 与名称、默认角色一并提交时全量替换部门管理员；不传则不改 */
  admin_user_ids?: number[]
  /** 为 true 时将当前默认角色（可与 default_role_ids 同次提交）授予本部门现存全部成员 */
  apply_default_roles_to_existing_members?: boolean
  /**
   * F062 节点标记：true=部门（对上级透明），false=机构（可见性隔离边界）。
   * 不传 = 不改。改动此字段需系统管理员/租户管理员权限，且提交前应先取影响面确认。
   */
  is_department?: boolean
}

/**
 * F062: how much a marker flip / node move would change.
 *
 * `unavailable` means the tenant is too large to evaluate — the UI must say so
 * rather than render 0, which would read as "this changes nothing".
 */
export interface NodeTypeImpact {
  affected_departments: number
  affected_libraries: number
  unavailable?: boolean
}
