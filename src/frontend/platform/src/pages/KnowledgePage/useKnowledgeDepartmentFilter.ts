import { useCallback, useContext, useEffect, useRef, useState } from "react"
import axios from "@/controllers/request"
import { captureAndAlertRequestErrorHoc } from "@/controllers/request"
import { userContext } from "@/contexts/userContext"
import { useLazyDepartmentTree } from "@/components/bs-comp/department"
// The controller and the node type are both exported under this name from the
// barrel (`LazyDepartmentTree` = controller type AND component). Import them
// from their source modules so the two roles stay unambiguous.
import type { LazyDepartmentTree } from "@/components/bs-comp/department/useLazyDepartmentTree"
import type { DepartmentSearchResult, DepartmentTreeNode } from "@/types/api/department"
import { getMyPrimaryDepartmentApi, searchMyDepartmentsApi } from "@/controllers/API/department"

/** F057: per-department library counts for the tree badges. */
async function getKnowledgeDepartmentStatsApi(params: {
  type?: number
  permissionId?: string
}): Promise<Array<{ department_id: number; count: number }>> {
  // The response interceptor in `@/controllers/request` already unwraps the
  // unified envelope: for `status_code === 200` it returns `response.data.data`,
  // so this resolves to the array itself. Reading `res.data` here would be
  // `undefined` on every call and silently blank every badge.
  const res = (await axios.get("/api/v1/knowledge/department_stats", {
    params: {
      type: params.type ?? 0,
      permission_id: params.permissionId ?? "view_kb",
    },
  })) as unknown as Array<{ department_id: number; count: number }> | null
  return res ?? []
}

/**
 * F057: department filter for the knowledge-base management page.
 *
 * Owns the left-hand tree, the current selection, and the per-department
 * library counts. The page layout is shared by BOTH tabs (file / Qa) at the
 * `KnowledgePage` level, so tab content receives this and only renders rows.
 *
 * Default selection is the current user's PRIMARY department, falling back to
 * "all" (no filter) when the user has none — the backend's own default would
 * otherwise be ambiguous, and a default that selects a node the user cannot see
 * is worse than no filter at all.
 */
export interface KnowledgeDepartmentFilter {
  tree: LazyDepartmentTree
  /** Numeric department id, or null for "all". */
  selectedDepartmentId: number | null
  /**
   * Business key (`BS@...`) of the selected department, or null for "all".
   *
   * `LazyDepartmentTree` compares its `selectedDeptId` prop against
   * `node.dept_id`, NOT against `node.id` — passing the numeric id leaves the
   * row unhighlighted. Kept alongside `selectedDepartmentId` because the
   * numeric id is what the list/tree APIs consume.
   */
  selectedDepartmentDeptId: string | null
  /** Ready-to-spread params for `readFileLibDatabase` — empty when unfiltered. */
  apiParams: { departmentId?: number; includeSubDepartments?: boolean }
  includeSubDepartments: boolean
  setIncludeSubDepartments: (value: boolean) => void
  onSelect: (node: DepartmentTreeNode) => void
  /**
   * Clear the department filter ("all libraries").
   *
   * Exposed for the "全部" row rendered above the tree: the tree itself holds
   * real departments only — no "all" and no "unclassified" node — so without
   * this there is no way back to the unfiltered list.
   */
  clearSelection: () => void
  /** department id -> library count (subtree-inclusive, matching the filter). */
  counts: Record<number, number>
  countsLoading: boolean
}

export function useKnowledgeDepartmentFilter(): KnowledgeDepartmentFilter {
  // Search goes to the server, like the management tree does. The tree is the
  // WHOLE tenant tree, so a client-side filter over the already-loaded layers
  // would miss every match below a collapsed node — the user types a department
  // name that is plainly in the organisation and is told there is no such thing.
  const fetchSearch = useCallback(
    async (keyword: string): Promise<DepartmentSearchResult> => searchMyDepartmentsApi(keyword),
    []
  )

  const tree = useLazyDepartmentTree({ fetchSearch, cacheKey: "dept:knowledge-browse" })
  const [selectedDepartmentId, setSelectedDepartmentId] = useState<number | null>(null)
  const [selectedDepartmentDeptId, setSelectedDepartmentDeptId] = useState<string | null>(null)
  const [includeSubDepartments, setIncludeSubDepartments] = useState(true)
  const [counts, setCounts] = useState<Record<number, number>>({})
  const [countsLoading, setCountsLoading] = useState(false)
  const { user } = useContext(userContext)
  // Guards the one-shot default-selection effect: it must run once, not on every
  // render, or a user's manual "all" choice would be overwritten.
  const defaultsAppliedRef = useRef(false)

  // Default to the user's primary department, highlighting its row in the tree.
  //
  // The row is already present: the chain endpoint returns the caller's
  // departments AND their ancestors, so the primary department is always in it
  // once its ancestors are expanded. `reveal()` is not used — it would call
  // `/departments/{id}/path-tree`, which is the management family that answers
  // 21009 for ordinary members.
  //
  // ADMINS ARE EXEMPT. An admin is not "a member of a department" in the sense
  // this filter means — the whole tree is theirs — and the account that seeded
  // this deployment sits in the guest department, which owns nothing. Applying
  // the default to them narrowed the list to a department with no libraries, so
  // the page looked empty even though the admin can read everything (the backend
  // returns None from `rebac_list_accessible` for admins, i.e. no filtering).
  // They start on "全部" and pick a department only if they want one.
  useEffect(() => {
    if (defaultsAppliedRef.current) return
    defaultsAppliedRef.current = true
    if (user?.role === 'admin') return
    void (async () => {
      try {
        const res = (await getMyPrimaryDepartmentApi()) as unknown as
          | Array<{ id: number; dept_id: string; name: string }>
          | { data?: Array<{ id: number; dept_id: string; name: string }> }
        const rows = Array.isArray(res) ? res : (res?.data ?? [])
        const primary = rows[0]
        const internalId = Number(primary?.id)
        if (!Number.isFinite(internalId) || internalId <= 0) return
        setSelectedDepartmentId(internalId)
        setSelectedDepartmentDeptId(primary?.dept_id ?? null)
      } catch {
        /* No primary department (or lookup failed) → stay on "all". */
      }
    })()
  }, [user?.role])

  // Per-department counts. Skipped when there is no department list to show —
  // an empty org tree should not cost a request.
  useEffect(() => {
    void (async () => {
      setCountsLoading(true)
      try {
        const list = await captureAndAlertRequestErrorHoc(getKnowledgeDepartmentStatsApi({}))
        const next: Record<number, number> = {}
        for (const row of list ?? []) next[Number(row.department_id)] = Number(row.count) || 0
        setCounts(next)
      } catch {
        /* Counts are an affordance; failing to load them must not break the list. */
      } finally {
        setCountsLoading(false)
      }
    })()
  }, [])

  const onSelect = useCallback((node: DepartmentTreeNode) => {
    setSelectedDepartmentId(node.id)
    setSelectedDepartmentDeptId(node.dept_id)
    setIncludeSubDepartments(true)
  }, [])

  const clearSelection = useCallback(() => {
    setSelectedDepartmentId(null)
    setSelectedDepartmentDeptId(null)
  }, [])

  return {
    tree,
    selectedDepartmentId,
    selectedDepartmentDeptId,
    apiParams:
      selectedDepartmentId == null
        ? {}
        : { departmentId: selectedDepartmentId, includeSubDepartments },
    includeSubDepartments,
    setIncludeSubDepartments,
    onSelect,
    clearSelection,
    counts,
    countsLoading,
  }
}
