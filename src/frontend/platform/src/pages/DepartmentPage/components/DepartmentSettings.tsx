// @ts-strict-ignore
import { bsConfirm } from "@/components/bs-ui/alertDialog/useConfirm"
import { Button } from "@/components/bs-ui/button"
import { Checkbox } from "@/components/bs-ui/checkBox"
import { Input } from "@/components/bs-ui/input"
import { Label } from "@/components/bs-ui/label"
import { RadioGroup, RadioGroupItem } from "@/components/bs-ui/radio-group"
import MultiSelect from "@/components/bs-ui/select/multi"
import { Separator } from "@/components/bs-ui/separator"
import { QuestionTooltip } from "@/components/bs-ui/tooltip"
import { toast } from "@/components/bs-ui/toast/use-toast"
import { TreeDepartmentSelect } from "@/components/bs-comp/department/TreeDepartmentSelect"
import DepartmentUsersSelect, {
  DepartmentUserOption,
} from "@/components/bs-comp/selectComponent/DepartmentUsersSelect"
import {
  deleteDepartmentApi,
  getDepartmentAdminsApi,
  getDepartmentApi,
  getDepartmentAssignableRolesApi,
  getDepartmentChildrenApi,
  getDepartmentPathTreeApi,
  getMoveImpactApi,
  getNodeTypeImpactApi,
  moveDepartmentApi,
  purgeDepartmentApi,
  restoreDepartmentApi,
  unmountTenantApi,
  updateDepartmentApi,
} from "@/controllers/API/department"
import { isGuestDepartmentDeptId } from "@/pages/DepartmentPage/constants/systemDepartments"
import { isSyncedSource } from "@/pages/DepartmentPage/constants/syncReadonly"
import { captureAndAlertRequestErrorHoc } from "@/controllers/request"
import { userContext } from "@/contexts/userContext"
import type {
  DepartmentAdmin,
  DepartmentSearchResult,
  DepartmentTreeNode,
  NodeTypeImpact,
} from "@/types/api/department"
import { Building2 } from "lucide-react"
import { useCallback, useContext, useEffect, useRef, useState } from "react"
import { useTranslation } from "react-i18next"

interface DepartmentSettingsProps {
  dept: DepartmentTreeNode
  onChanged: (removedDeptId?: string) => void
  /** Open the "mark as Child Tenant" dialog for this department. When undefined
   * (multi-tenant disabled or root dept), the action button is hidden. */
  onMarkAsTenant?: (deptId: number, deptName: string) => void
}

function adminsToOptions(admins: DepartmentAdmin[]): DepartmentUserOption[] {
  return admins.map((a) => ({ value: Number(a.user_id), label: a.user_name }))
}

function sameIdSet(left: Array<number | string>, right: Array<number | string>): boolean {
  if (left.length !== right.length) return false
  const leftIds = left.map(String).sort()
  const rightIds = right.map(String).sort()
  return leftIds.every((id, idx) => id === rightIds[idx])
}

/** 企业级表单：统一控件最大宽度，右侧对齐 */
const FORM_CONTROL_WIDTH = "w-full max-w-md"

export function DepartmentSettings({ dept, onChanged, onMarkAsTenant }: DepartmentSettingsProps) {
  const { t } = useTranslation()
  const { user } = useContext(userContext) as {
    user: { role?: string; is_department_admin?: boolean; is_child_admin?: boolean } | null
  }
  const [name, setName] = useState(dept.name)
  // F062 节点标记。用 "org"/"dept" 而非布尔：单选组的 value 是字符串，且
  // 「机构」才是默认态，写成 true/false 会让「未设置」和「显式设为机构」难以区分。
  const [nodeType, setNodeType] = useState<"org" | "dept">(dept.is_department ? "dept" : "org")
  const [adminSelectValue, setAdminSelectValue] = useState<DepartmentUserOption[]>([])
  const [defaultRoleIds, setDefaultRoleIds] = useState<string[]>([])
  const [applyDefaultRolesToExisting, setApplyDefaultRolesToExisting] = useState(false)
  const [assignableRoles, setAssignableRoles] = useState<{ value: string; label: string }[]>([])
  const [saving, setSaving] = useState(false)
  const [loading, setLoading] = useState(true)
  const [parentIdValue, setParentIdValue] = useState<number | null>(dept.parent_id ?? null)
  // F038: the full tree is no longer passed in. ``isVisibleRootDept`` is derived
  // from the root layer; ``parentDisplayName`` from a path-tree lookup (used for
  // the read-only parent field when the parent can't be edited).
  const [isVisibleRootDept, setIsVisibleRootDept] = useState(false)
  // Whether the visible-root determination (below) has completed for the current
  // dept. ``isVisibleRootDept`` starts false, so without this gate the editable
  // parent picker would mount for one render on a visible-root dept and locate its
  // OUT-OF-SCOPE parent via path-tree (21009). Keep the parent read-only until we
  // actually know whether this dept is a root.
  const [visibleRootResolved, setVisibleRootResolved] = useState(false)
  const [parentDisplayName, setParentDisplayName] = useState("-")

  const adminSelectValueRef = useRef<DepartmentUserOption[]>([])

  const isSynced = isSyncedSource(dept.source)
  const isArchived = dept.status === "archived"
  const isAbsoluteRootDept = dept.parent_id === null || Number(dept.parent_id) === 0
  // 对部门管理员场景：当前可见树的顶层节点也视为“根节点”（即便全局树里它还有父节点）。
  // 租户根部门同样按“根”对待：其父是系统根——对租户管理员越权、对任何角色都无展示意义，
  // 故隐藏“上级部门”字段、不取父名、也不允许移动（与查看者角色无关）。
  const isRootDept = isAbsoluteRootDept || isVisibleRootDept || !!dept.is_tenant_root
  /** 仅部门名称对第三方同步部门只读；管理员与默认角色仍可保存 */
  const canEditName = !isArchived && !isSynced
  const canEditPermissions = !isArchived
  const canEditParent = !isArchived && !isSynced && !isRootDept && visibleRootResolved
  /**
   * F062: the marker is gated ABOVE ordinary department admin (backend enforces
   * this — see `_ensure_may_change_node_type`). The UI mirrors the rule so an
   * admin is not offered a control that would 403.
   *
   * Best-effort only, and deliberately so: the backend is the authority. This
   * mirrors the signal the platform uses for admin-console reach everywhere else
   * — `is_child_admin` is the tenant-admin flag (see MainLayout / HeaderMenu /
   * ModelPage.permissions); there is no `is_tenant_admin` field on the user
   * payload, and testing for it would read `undefined` and permanently disable
   * the control for the very people the backend admits.
   *
   * `is_department_admin` is NOT included: the backend refuses them (that is the
   * escalation this feature closes), so offering the control would only produce
   * a 403 after the confirmation dialog.
   */
  const canEditNodeType =
    !isArchived && (user?.role === "admin" || Boolean(user?.is_child_admin))

  /** 最近一次从服务端加载成功的快照（父部门变更判断、保存后更新） */
  const baselineRef = useRef<{
    name: string
    admins: DepartmentUserOption[]
    defaultRoleIds: string[]
    parentId: number | null
    /** F062: the marker as loaded, so a change can be detected and confirmed. */
    isDepartment: boolean
  } | null>(null)

  useEffect(() => {
    adminSelectValueRef.current = adminSelectValue
  }, [adminSelectValue])

  // F038: resolve "is this the top of the viewer's visible tree?" from the root
  // layer, and the parent's display name from a path-tree lookup — replacing the
  // walks over a full tree that's no longer passed in. The move picker forbids
  // selecting the dept's own subtree via excludeSubtreePath={dept.path} instead.
  useEffect(() => {
    let cancelled = false
    // Re-defer the editable parent picker until this dept's root-ness is known.
    setVisibleRootResolved(false)
    if (dept.parent_id == null || dept.is_tenant_root) {
      // Absolute root, or a tenant root whose parent is the system root: there is
      // no in-scope / meaningful parent to resolve. ``isRootDept`` already covers
      // both via ``is_tenant_root`` so the parent field is hidden — skip the fetch
      // entirely (a tenant admin would be denied; a super admin doesn't need it).
      setIsVisibleRootDept(false)
      setParentDisplayName("-")
      setVisibleRootResolved(true)
      return
    }
    captureAndAlertRequestErrorHoc(getDepartmentChildrenApi(null)).then((roots) => {
      if (cancelled) return
      const visibleRoot = Array.isArray(roots) && roots.some((n) => n.id === dept.id)
      setIsVisibleRootDept(visibleRoot)
      setVisibleRootResolved(true)
      // When the dept sits at the top of the viewer's visible tree, its parent is
      // OUTSIDE the viewer's scope (e.g. a sub-tenant admin's tenant-root dept,
      // whose parent is the global root). Fetching that parent's path-tree would
      // be denied (21009) — skip it and leave the read-only parent field blank.
      // A nested dept's parent is always in scope, so it is safe to resolve.
      if (visibleRoot) {
        setParentDisplayName("-")
        return
      }
      captureAndAlertRequestErrorHoc(getDepartmentPathTreeApi(dept.parent_id!)).then((res) => {
        if (cancelled) return
        const pruned = (res as DepartmentSearchResult | null) ?? null
        let cur = pruned?.roots?.[0]
        let name = "-"
        while (cur) {
          name = cur.name
          cur = cur.children?.[0]
        }
        setParentDisplayName(name)
      })
    })
    return () => {
      cancelled = true
    }
  }, [dept.id, dept.parent_id, dept.is_tenant_root])

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    setApplyDefaultRolesToExisting(false)
    Promise.all([
      getDepartmentAdminsApi(dept.dept_id),
      getDepartmentApi(dept.dept_id),
      getDepartmentAssignableRolesApi(dept.dept_id),
    ])
      .then(([adminsRes, detailRes, rolesRes]) => {
        if (cancelled) return
        const adm = Array.isArray(adminsRes) ? adminsRes : []
        const adminOpts = adminsToOptions(adm)
        setAdminSelectValue(adminOpts)
        adminSelectValueRef.current = adminOpts
        setName(detailRes?.name ?? dept.name)
        const dr = (detailRes?.default_role_ids ?? []).map(String)
        setDefaultRoleIds(dr)
        const pid = detailRes?.parent_id ?? dept.parent_id ?? null
        setParentIdValue(pid)
        // F062: re-sync the marker with the node being loaded. This component is
        // NOT remounted when the operator picks a different node in the tree
        // (no `key` at the call site), so without this the previous node's marker
        // persists — and `markerChanged` then reads as "the operator changed it"
        // on a node they never touched. Saving a mere rename would submit
        // `is_department: false` and silently flip that node to 机构, or (the
        // other direction) widen visibility by flipping 机构 to 部门.
        setNodeType((detailRes?.is_department ?? dept.is_department) ? "dept" : "org")
        setAssignableRoles(
          (rolesRes || []).map((r) => ({ value: String(r.id), label: r.role_name }))
        )
        baselineRef.current = {
          name: detailRes?.name ?? dept.name,
          admins: adminOpts,
          defaultRoleIds: dr,
          parentId: pid,
          isDepartment: Boolean(detailRes?.is_department ?? dept.is_department),
        }
      })
      .catch(() => {
        if (!cancelled) {
          toast({
            title: t("prompt"),
            variant: "error",
            description: t("bs:department.settingsLoadFailed"),
          })
        }
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [dept.dept_id, dept.name, dept.parent_id, dept.is_department, t])

  const restoreBaseline = useCallback((b = baselineRef.current) => {
    if (!b) return
    setName(b.name)
    setAdminSelectValue(b.admins)
    adminSelectValueRef.current = b.admins
    setDefaultRoleIds(b.defaultRoleIds)
    setParentIdValue(b.parentId)
    // F062: the marker is part of the baseline too — leaving it out meant
    // "discard my edits" kept the marker change and would resubmit it.
    setNodeType(b.isDepartment ? "dept" : "org")
  }, [])

  /**
   * F062 (AC-05): show the blast radius before letting a marker change through.
   *
   * The value moves one bit; what it changes is who can read whose knowledge.
   * Three distinct outcomes must be presented differently, because conflating
   * them would mislead the operator:
   *   - a count      → "N departments / M libraries"
   *   - zero         → "changes nothing" (legitimate: flipping a leaf)
   *   - unavailable  → "cannot estimate", NEVER rendered as 0, which would read
   *                    as "harmless" for a tenant where we simply cannot say.
   */
  /**
   * F062 (AC-05): shared confirm flow for the two tree changes that move nodes
   * between org domains — flipping the marker and moving the node.
   *
   * `fetchImpact` is injected so both paths render an identical dialog from
   * whichever preview applies; only the request differs. Both are read-only and
   * both return the same shape, `unavailable` included.
   */
  const confirmImpact = useCallback(
    async (fetchImpact: () => Promise<NodeTypeImpact | null>) => {
      let impact: NodeTypeImpact | null = null
      try {
        impact = await fetchImpact()
      } catch {
        // Treated as "cannot estimate" below rather than aborting: the operator
        // has already decided, and the backend still enforces permission.
      }

      let detail: string
      if (!impact || impact.unavailable) {
        detail = t("bs:department.impactUnavailable")
      } else if (impact.affected_departments === 0 && impact.affected_libraries === 0) {
        detail = t("bs:department.impactZero")
      } else {
        detail = t("bs:department.impactBody", {
          departments: impact.affected_departments,
          libraries: impact.affected_libraries,
        })
      }

      return new Promise<boolean>((resolve) => {
      // `bsConfirm` is callback-based and offers THREE ways out: ok, cancel,
      // and close (the X / backdrop / Esc, which fire `onClose` only). Wiring
      // just onOk/onCancel leaves the close path unresolved and the awaiting
      // save handler hangs forever with the button stuck. `settle` makes every
      // exit resolve exactly once.
      let settled = false
      const settle = (v: boolean) => {
        if (settled) return
        settled = true
        resolve(v)
      }
        bsConfirm({
          title: t("bs:department.impactTitle"),
          desc: detail,
          okTxt: t("bs:department.impactConfirm"),
          onOk: () => settle(true),
          onCancel: () => settle(false),
          onClose: () => settle(false),
        })
      })
    },
    [t],
  )

  const confirmNodeTypeChange = useCallback(
    () => confirmImpact(async () => (await getNodeTypeImpactApi(dept.dept_id)) as NodeTypeImpact),
    [confirmImpact, dept.dept_id],
  )

  const confirmMove = useCallback(
    (newParentId: number) =>
      confirmImpact(async () => (await getMoveImpactApi(dept.dept_id, newParentId)) as NodeTypeImpact),
    [confirmImpact, dept.dept_id],
  )

  const hasUnsavedSettingsChanges = useCallback(() => {
    const b = baselineRef.current
    if (!b) return false
    if (canEditName && name !== b.name) return true
    if (canEditParent && parentIdValue !== b.parentId) return true
    if (canEditNodeType && nodeType !== (b.isDepartment ? "dept" : "org")) return true
    if (
      !sameIdSet(
        adminSelectValue.map((o) => o.value),
        b.admins.map((o) => o.value)
      )
    ) return true
    if (!sameIdSet(defaultRoleIds, b.defaultRoleIds)) return true
    return false
  }, [
    adminSelectValue,
    canEditName,
    canEditNodeType,
    canEditParent,
    defaultRoleIds,
    name,
    nodeType,
    parentIdValue,
  ])

  const handleCancel = useCallback(() => {
    const b = baselineRef.current
    if (!b) return
    if (!hasUnsavedSettingsChanges()) {
      restoreBaseline(b)
      return
    }
    bsConfirm({
      title: t("prompt"),
      desc: t("department.confirmCancelSettings", { ns: "bs" }),
      onOk: (next) => {
        restoreBaseline(b)
        next()
      },
    })
  }, [hasUnsavedSettingsChanges, restoreBaseline, t])

  const handleGlobalSave = useCallback(async () => {
    if (!canEditPermissions) return
    if (canEditName && (!name || name.length < 2 || name.length > 50)) {
      toast({
        title: t("prompt"),
        description: t("bs:department.nameLength"),
        variant: "error",
      })
      return
    }
    // F062: `setSaving` is deliberately NOT set before the confirm dialogs
    // below. The shared `bsConfirm` resolves its Promise only through ok /
    // cancel / the X icon — dismissing with Esc goes straight to Radix's
    // `onOpenChange(setOpen)` (useConfirm.tsx:53) and fires NEITHER callback, so
    // an `await` on it would never return. Setting the flag first would leave
    // the save button disabled forever after an Esc; leaving it until after the
    // dialogs means the worst case is "this save did not happen", which the
    // operator can simply retry. (Fixing the shared component is out of scope
    // for this feature — every bsConfirm caller has the same behaviour.)
    try {
      const body: {
        name?: string
        default_role_ids?: number[]
        admin_user_ids?: number[]
        apply_default_roles_to_existing_members?: boolean
        /** F062: only set when the marker actually changed AND the impact dialog was confirmed. */
        is_department?: boolean
      } = {}
      const baseline = baselineRef.current
      const nextName = name.trim()
      const nextAdminIds = adminSelectValue.map((o) => o.value)
      const nextDefaultRoleIds = defaultRoleIds.map(Number)
      if (canEditName && baseline && nextName !== baseline.name) {
        body.name = nextName
      }
      if (
        baseline &&
        !sameIdSet(
          nextAdminIds,
          baseline.admins.map((o) => o.value)
        )
      ) {
        body.admin_user_ids = nextAdminIds
      }
      if (baseline && !sameIdSet(nextDefaultRoleIds, baseline.defaultRoleIds)) {
        body.default_role_ids = nextDefaultRoleIds
      }
      if (applyDefaultRolesToExisting) {
        body.apply_default_roles_to_existing_members = true
      }
      // F062: the marker change goes LAST and is gated by the impact dialog.
      // Submitting it silently would be the one edit on this form whose effect
      // is invisible in the UI but very visible in who can read what.
      const markerChanged = canEditNodeType && baseline && nodeType !== (baseline.isDepartment ? "dept" : "org")
      if (markerChanged) {
        const confirmed = await confirmNodeTypeChange()
        if (!confirmed) return
        body.is_department = nodeType === "dept"
      }
      const nextParentId = parentIdValue
      const parentChanged =
        canEditParent &&
        baseline &&
        nextParentId !== null &&
        nextParentId !== baseline.parentId

      if (!parentChanged && Object.keys(body).length === 0) {
        return
      }

      // Now that every confirm has been answered, this save is really going to
      // issue requests — see the note above on why the flag waits until here.
      setSaving(true)

      if (parentChanged) {
        // F062 (AC-05): a move rewrites the subtree's materialized path, so it
        // relocates every node under it between org domains — the same class of
        // blast radius as a marker flip, and previously submitted with no
        // confirmation at all.
        const confirmed = await confirmMove(nextParentId)
        if (!confirmed) return
        const moveRes = await captureAndAlertRequestErrorHoc(
          moveDepartmentApi(dept.dept_id, nextParentId)
        )
        if (moveRes === null || moveRes === false) return
      }
      const res = await captureAndAlertRequestErrorHoc(
        updateDepartmentApi(dept.dept_id, body)
      )
      if (res === null || res === false) return
      toast({
        title: t("prompt"),
        description: t("saved"),
        variant: "success",
      })
      setApplyDefaultRolesToExisting(false)
      const nextAdmins = await getDepartmentAdminsApi(dept.dept_id).catch(() => null)
      const adminOpts = Array.isArray(nextAdmins)
        ? adminsToOptions(nextAdmins)
        : adminSelectValue
      setAdminSelectValue(adminOpts)
      adminSelectValueRef.current = adminOpts
      baselineRef.current = {
        name: nextName,
        admins: adminOpts,
        defaultRoleIds: [...defaultRoleIds],
        parentId: nextParentId ?? baselineRef.current?.parentId ?? dept.parent_id ?? null,
        isDepartment: nodeType === "dept",
      }
      onChanged()
    } finally {
      setSaving(false)
    }
  }, [
    adminSelectValue,
    canEditName,
    canEditNodeType,
    canEditParent,
    canEditPermissions,
    applyDefaultRolesToExisting,
    confirmMove,
    confirmNodeTypeChange,
    defaultRoleIds,
    dept.dept_id,
    dept.parent_id,
    name,
    nodeType,
    onChanged,
    parentIdValue,
    t,
  ])

  const handleDelete = useCallback(() => {
    bsConfirm({
      title: t("bs:department.delete"),
      desc: t("bs:department.confirmDelete"),
      onOk: (next) => {
        captureAndAlertRequestErrorHoc(deleteDepartmentApi(dept.dept_id)).then((res) => {
          if (res !== false && res !== "canceled") {
            toast({
              title: t("prompt"),
              description: t("deleteSuccess"),
              variant: "success",
            })
            onChanged()
          }
          next()
        })
      },
    })
  }, [dept.dept_id, onChanged, t])

  const handlePurge = useCallback(() => {
    bsConfirm({
      title: t("bs:department.permanentDelete"),
      desc: t("bs:department.confirmPermanentDelete"),
      onOk: (next) => {
        captureAndAlertRequestErrorHoc(purgeDepartmentApi(dept.dept_id)).then((res) => {
          if (res !== false && res !== "canceled") {
            toast({
              title: t("prompt"),
              description: t("deleteSuccess"),
              variant: "success",
            })
            onChanged(dept.dept_id)
          }
          next()
        })
      },
    })
  }, [dept.dept_id, onChanged, t])

  const handleRestore = useCallback(() => {
    bsConfirm({
      title: t("bs:department.restore"),
      desc: t("bs:department.confirmRestore"),
      onOk: (next) => {
        captureAndAlertRequestErrorHoc(restoreDepartmentApi(dept.dept_id)).then((res) => {
          if (res !== false && res !== "canceled") {
            toast({
              title: t("prompt"),
              description: t("save") + t("success"),
              variant: "success",
            })
            onChanged()
          }
          next()
        })
      },
    })
  }, [dept.dept_id, onChanged, t])

  return (
    <div className="max-w-3xl pb-8">
      {isArchived && (
        <div className="mb-4 rounded-md border border-orange-200 bg-orange-50 p-3 text-sm text-orange-800 dark:border-orange-800 dark:bg-orange-950 dark:text-orange-200">
          {t("bs:department.archivedNotice")}
        </div>
      )}

      {loading && (
        <p className="mb-4 text-sm text-muted-foreground">{t("loading", { ns: "bs" })}</p>
      )}

      {/* 区块一：基础信息 */}
      <section className="space-y-4">
        <div>
          <h3 className="mb-2 text-base font-semibold tracking-tight text-foreground">
            {t("bs:department.sectionBasic")}
          </h3>
          <Separator />
        </div>
        <div className="space-y-1.5">
          <Label>{t("bs:department.name")}</Label>
          {isSynced || isArchived ? (
            <Input value={name} disabled className={FORM_CONTROL_WIDTH} />
          ) : (
            <Input
              value={name}
              onChange={(e) => setName(e.target.value)}
              maxLength={50}
              className={FORM_CONTROL_WIDTH}
            />
          )}
        </div>
        {!isRootDept && (
          <div className="space-y-1.5">
            <Label>{t("bs:department.parentDept")}</Label>
            {canEditParent ? (
              <TreeDepartmentSelect
                value={parentIdValue}
                onChange={(id) => setParentIdValue(id)}
                excludeSubtreePath={dept.path}
                className={FORM_CONTROL_WIDTH}
                placeholder={t("bs:department.selectDept")}
                searchPlaceholder={t("bs:department.parentDept")}
                modal={false}
              />
            ) : (
              <Input value={parentDisplayName} disabled className={FORM_CONTROL_WIDTH} />
            )}
          </div>
        )}
        {/* F062: the 机构 / 部门 marker. Sits in the BASIC section because it
            describes what the node IS, not what it may do — the permission
            section below is about who may edit it. The two hints differ because
            the choice is genuinely consequential in both directions: 机构 is a
            visibility boundary, 部门 makes the node transparent to its ancestors. */}
        <div className="space-y-1.5">
          <Label>{t("bs:department.nodeTypeLabel")}</Label>
          <RadioGroup
            value={nodeType}
            onValueChange={(v) => setNodeType(v as "org" | "dept")}
            disabled={!canEditNodeType}
            className="gap-2"
          >
            <div className="flex items-start gap-2">
              <RadioGroupItem value="org" id="f062-node-type-org" className="mt-0.5" />
              <div className="space-y-0.5">
                <Label htmlFor="f062-node-type-org" className="font-normal">
                  {t("bs:department.nodeTypeOrg")}
                </Label>
                <p className="text-xs text-text-3">{t("bs:department.nodeTypeOrgHint")}</p>
              </div>
            </div>
            <div className="flex items-start gap-2">
              <RadioGroupItem value="dept" id="f062-node-type-dept" className="mt-0.5" />
              <div className="space-y-0.5">
                <Label htmlFor="f062-node-type-dept" className="font-normal">
                  {t("bs:department.nodeTypeDept")}
                </Label>
                <p className="text-xs text-text-3">{t("bs:department.nodeTypeDeptHint")}</p>
              </div>
            </div>
          </RadioGroup>
        </div>
      </section>

      {/* 区块二：权限与角色 */}
      {!isArchived && (
        <section className="mt-6 space-y-4">
          <div>
            <h3 className="mb-2 text-base font-semibold tracking-tight text-foreground">
              {t("bs:department.sectionPermissions")}
            </h3>
            <Separator />
          </div>
          <div className="space-y-1.5">
            <Label>{t("bs:department.admins")}</Label>
            <DepartmentUsersSelect
              multiple
              disabled={!canEditPermissions}
              value={adminSelectValue}
              onChange={(vals) => {
                const v = (vals as DepartmentUserOption[]) || []
                setAdminSelectValue(v)
                adminSelectValueRef.current = v
              }}
              placeholder={t("bs:department.adminSelectPlaceholder")}
              searchPlaceholder={t("bs:department.searchUsersPlaceholder")}
              className={FORM_CONTROL_WIDTH}
              // Tenant-root depts: admin assignment writes the FGA
              // ``admin tenant:X`` tuple, so we constrain the picker to the
              // dept's own subtree. Plain depts keep the full org tree.
              rootDeptId={dept.is_tenant_root ? dept.id : undefined}
              emptyMessage={
                dept.is_tenant_root
                  ? t("bs:tenant.initialAdminEmptySubtree", {
                      defaultValue:
                        "该部门子树暂无成员，请先把目标管理员加入此部门后再挂载",
                    })
                  : undefined
              }
            />
            {dept.is_tenant_root ? (
              <p className="mt-1 max-w-md text-xs leading-snug text-gray-500 dark:text-gray-400">
                {t("bs:tenant.initialAdminSubtreeHint", {
                  defaultValue: "管理员必须来自该部门子树，不能选取子树外用户。",
                })}
              </p>
            ) : null}
          </div>
          <div className="space-y-1.5">
            <Label>{t("bs:department.defaultRoles")}</Label>
            <MultiSelect
              multiple
              value={defaultRoleIds}
              options={assignableRoles}
              placeholder={t("bs:department.selectRoles")}
              onChange={(vals) => setDefaultRoleIds(vals as string[])}
              disabled={!canEditPermissions}
              className={FORM_CONTROL_WIDTH}
            />
            <label className="mt-2 flex max-w-md cursor-pointer items-start gap-2 text-sm leading-snug">
              <Checkbox
                className="mt-0.5"
                checked={applyDefaultRolesToExisting}
                disabled={!canEditPermissions}
                onCheckedChange={(v) => setApplyDefaultRolesToExisting(Boolean(v))}
              />
              <span className="flex flex-1 flex-wrap items-center gap-1.5 text-foreground">
                {t("bs:department.applyDefaultRolesToExisting")}
                <span
                  className="inline-flex shrink-0"
                  onClick={(e) => e.preventDefault()}
                  onPointerDown={(e) => e.stopPropagation()}
                >
                  <QuestionTooltip
                    className="text-muted-foreground hover:text-foreground"
                    content={t("bs:department.applyDefaultRolesToExistingTooltip")}
                  />
                </span>
              </span>
            </label>
            <p className="mt-1 max-w-md text-xs leading-snug text-gray-500 dark:text-gray-400">
              {t("bs:department.defaultRolesHint")}
            </p>
          </div>
        </section>
      )}

      {/* 已归档：还原 / 永久删除 */}
      {isArchived && (
        <div className="mt-5 border-t pt-4">
          <div className="flex flex-wrap items-center gap-2">
            <Button variant="outline" onClick={handleRestore}>
              {t("bs:department.restore")}
            </Button>
            {!isGuestDepartmentDeptId(dept.dept_id) ? (
              <Button variant="destructive" onClick={handlePurge}>
                {t("bs:department.permanentDelete")}
              </Button>
            ) : null}
          </div>
        </div>
      )}

      {/* 全局保存 + 删除部门 */}
      {!isArchived && (
        <div className="mt-5 border-t pt-4">
          <div className="flex flex-wrap items-end gap-3">
            <div className="flex flex-wrap items-center gap-2">
              {canEditPermissions && (
                <Button type="button" onClick={() => void handleGlobalSave()} disabled={saving}>
                  {t("save")}
                </Button>
              )}
            </div>
            <div className="min-w-[1rem] flex-1" />
            {onMarkAsTenant && !isRootDept && !dept.is_tenant_root && (
              <Button
                variant="outline"
                onClick={() => onMarkAsTenant(dept.id, dept.name)}
                className="shrink-0"
              >
                <Building2 className="mr-1.5 h-4 w-4" />
                {t("bs:tenant.markAsTenant", { defaultValue: "标记为子租户" })}
              </Button>
            )}
            {onMarkAsTenant && !isRootDept && dept.is_tenant_root && (
              <Button
                variant="outline"
                onClick={() => {
                  bsConfirm({
                    title: t("bs:tenant.unmountTitle", { defaultValue: "取消挂载子租户" }),
                    desc: t("bs:tenant.unmountConfirm", {
                      defaultValue:
                        "解除挂载后，该子租户名下的所有资源（知识库、应用、会话等）将自动迁移到集团总部（Root），子租户本身归档保留供审计。子租户成员将作为总部成员继续使用，业务不中断。是否继续？",
                    }),
                    okTxt: t("bs:tenant.unmountOk", { defaultValue: "确认取消挂载" }),
                    onOk(next) {
                      captureAndAlertRequestErrorHoc(
                        unmountTenantApi(dept.id)
                      ).then((res) => {
                        if (res) {
                          toast({
                            title: t("bs:tenant.unmountSuccess", {
                              defaultValue: "已取消挂载",
                            }),
                            variant: "success",
                          })
                          onChanged()
                        }
                        next()
                      })
                    },
                  })
                }}
                className="shrink-0"
              >
                <Building2 className="mr-1.5 h-4 w-4" />
                {t("bs:tenant.unmount", { defaultValue: "取消挂载" })}
              </Button>
            )}
            {!isSynced &&
              dept.parent_id !== null &&
              !isGuestDepartmentDeptId(dept.dept_id) && (
              <Button variant="destructive" onClick={handleDelete} className="shrink-0">
                {t("bs:department.delete")}
              </Button>
            )}
          </div>
        </div>
      )}
    </div>
  )
}
