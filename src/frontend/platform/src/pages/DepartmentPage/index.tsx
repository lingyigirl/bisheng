import { LazyDepartmentTree, useLazyDepartmentTree } from "@/components/bs-comp/department"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/bs-ui/tabs"
import { Button } from "@/components/bs-ui/button"
import { bsConfirm } from "@/components/bs-ui/alertDialog/useConfirm"
import {
  batchSetNodeTypeApi,
  getDepartmentApi,
  getNodeTypeImpactApi,
} from "@/controllers/API/department"
import { captureAndAlertRequestErrorHoc } from "@/controllers/request"
import { locationContext } from "@/contexts/locationContext"
import { userContext } from "@/contexts/userContext"
import { DepartmentTreeNode, NodeTypeImpact } from "@/types/api/department"
import { Plus } from "lucide-react"
import { useCallback, useContext, useEffect, useRef, useState } from "react"
import { useTranslation } from "react-i18next"
import { CreateDepartmentDialog } from "./components/CreateDepartmentDialog"
import { DepartmentSettings } from "./components/DepartmentSettings"
import { MemberTable } from "./components/MemberTable"
import { NodeTypeGuideBanner } from "./components/NodeTypeGuideBanner"
import { MountTenantDialog } from "./components/MountTenantDialog"

export default function DepartmentPage() {
  const { t } = useTranslation()
  const { appConfig } = useContext(locationContext)
  const { user } = useContext(userContext)
  const multiTenantEnabled = !!appConfig?.multiTenantEnabled
  const canMountTenant = multiTenantEnabled && !!user?.is_global_super

  // F038: management nav tree is now lazy (root layer first, children on expand,
  // server-side search/locate). includeArchived: the management tree shows
  // archived nodes (AC-16); autoExpandRoots mirrors the old "first level open".
  const tree = useLazyDepartmentTree({ includeArchived: true, autoExpandRoots: true })

  // Track the selection by internal id and derive the live node from the tree so
  // a rename/move refresh keeps the right panel in sync; keep a snapshot as a
  // fallback for when the node isn't in a currently-loaded layer.
  const [selectedId, setSelectedId] = useState<number | null>(null)
  const [selectedSnapshot, setSelectedSnapshot] = useState<DepartmentTreeNode | null>(null)
  const selectedDept = (selectedId != null ? tree.getNode(selectedId) : null) ?? selectedSnapshot
  const selectedDeptId = selectedDept?.dept_id ?? null

  const [createOpen, setCreateOpen] = useState(false)
  const [createParentId, setCreateParentId] = useState<number | null>(null)
  const [mountTarget, setMountTarget] = useState<{ id: number; name: string } | null>(null)
  const [membersRefreshSignal, setMembersRefreshSignal] = useState(0)
  const [memberHighlightUserId, setMemberHighlightUserId] = useState<number | null>(null)
  // F062: the guide banner's "go annotate" needs to switch the right panel to
  // the settings tab, which requires the Tabs to be controlled.
  const [activeTab, setActiveTab] = useState("members")

  /**
   * F062 (AC-08) batch annotation.
   *
   * Selection is keyed by `dept_id` (the business key every endpoint takes), not
   * the internal id, so the payload needs no translation and a stale node cannot
   * be addressed by a recycled PK.
   *
   * Only rows the operator may actually annotate get a checkbox — offering one
   * on a node the backend refuses would produce a dialog, a confirmation, and
   * then a 403 for the whole batch.
   */
  const [batchMode, setBatchMode] = useState(false)
  const [batchSelected, setBatchSelected] = useState<Set<string>>(new Set())
  const [batchSaving, setBatchSaving] = useState(false)

  /**
   * F062: does the tenant have ANY node annotated as 部门 yet?
   *
   * Best-effort, over the nodes currently LOADED by the lazy tree — the
   * controller exposes `getNode` per id without a full traversal, and adding a
   * backend endpoint just for a hint would not earn its keep. The banner is
   * advisory: as the operator expands more of the tree the answer sharpens, and
   * the false-negative direction (saying "none" when an unloaded node has one)
   * merely leaves a dismissible hint on screen.
   */
  const hasAnyDepartmentLoaded = [
    ...tree.rootIds,
    ...Array.from(tree.expanded).flatMap((id) => tree.getChildIds(id) ?? []),
  ].some((id) => tree.getNode(id)?.is_department)
  const rowRefs = useRef<Map<string, HTMLDivElement>>(new Map())

  const selectNode = useCallback((node: DepartmentTreeNode) => {
    setSelectedId(node.id)
    setSelectedSnapshot(node)
  }, [])

  // Auto-select the first visible root once the root layer loads.
  useEffect(() => {
    if (selectedId == null && tree.rootIds.length > 0) {
      const first = tree.getNode(tree.rootIds[0])
      if (first) selectNode(first)
    }
  }, [tree.rootIds, selectedId, tree, selectNode])

  const handleCreateClick = useCallback((parentId?: number) => {
    setCreateParentId(parentId ?? null)
    setCreateOpen(true)
  }, [])

  const handleCreated = useCallback(() => {
    setCreateOpen(false)
    void tree.reloadLayer(createParentId)
  }, [tree, createParentId])

  const handleMarkAsTenant = useCallback((deptId: number, deptName: string) => {
    setMountTarget({ id: deptId, name: deptName })
  }, [])

  const handleMounted = useCallback(() => {
    setMountTarget(null)
    void tree.refreshAll()
  }, [tree])

  // Create / move / archive / restore / rename: refresh affected (loaded) layers
  // — a move touches both old and new parent, so refresh all loaded layers (AC-05).
  const handleTreeChange = useCallback(
    async (removedDeptId?: string) => {
      // Await the refresh first so, when the removed dept was selected, the
      // auto-select effect picks the FRESH first root — not the stale one.
      await tree.refreshAll()
      if (removedDeptId && selectedDeptId === removedDeptId) {
        setSelectedId(null)
        setSelectedSnapshot(null)
      }
    },
    [tree, selectedDeptId]
  )

  const handleDepartmentSettingsChanged = useCallback(
    (removedDeptId?: string) => {
      handleTreeChange(removedDeptId)
      setMembersRefreshSignal((prev) => prev + 1)
    },
    [handleTreeChange]
  )

  const handleLocateMemberFromGlobal = useCallback(
    async ({ primaryDeptDeptId, userId }: { primaryDeptDeptId: string; userId: number }) => {
      setMemberHighlightUserId(userId)
      // The global search row carries the business dept_id; resolve its internal
      // id, then reveal (expand + load) the branch and scroll to it.
      const detail = await captureAndAlertRequestErrorHoc(getDepartmentApi(primaryDeptDeptId))
      if (!detail) return
      await tree.reveal(detail.id)
      const node = tree.getNode(detail.id)
      if (node) selectNode(node)
      window.setTimeout(() => {
        rowRefs.current.get(primaryDeptDeptId)?.scrollIntoView({ block: "nearest", behavior: "smooth" })
      }, 80)
    },
    [tree, selectNode]
  )

  const handleMemberHighlightConsumed = useCallback(() => setMemberHighlightUserId(null), [])

  /** F062: only nodes the operator may annotate get a checkbox. */
  const canAnnotate = user?.role === "admin" || Boolean(user?.is_child_admin)
  const toggleBatch = useCallback((deptId: string) => {
    setBatchSelected((prev) => {
      const next = new Set(prev)
      if (next.has(deptId)) next.delete(deptId)
      else next.add(deptId)
      return next
    })
  }, [])

  const renderRowCheckbox = useCallback(
    (node: DepartmentTreeNode) => (
      <input
        type="checkbox"
        className="ml-1 mr-1.5 h-3.5 w-3.5 shrink-0 cursor-pointer"
        checked={batchSelected.has(node.dept_id)}
        disabled={node.status === "archived"}
        onClick={(e) => e.stopPropagation()}
        onChange={() => toggleBatch(node.dept_id)}
        aria-label={node.name}
      />
    ),
    [batchSelected, toggleBatch],
  )

  /**
   * F062 (AC-08): apply the marker to the whole selection, after confirming the
   * blast radius.
   *
   * The confirm is the SAME dialog as the single-node flip (`impactBody` /
   * `impactZero` / `impactUnavailable`); here the impact is the SUM over the
   * selection, so the operator sees one number for one intention rather than
   * clicking through N dialogs.
   *
   * The batch itself is all-or-nothing server-side — see `batchSetNodeTypeApi`.
   */
  const handleBatchApply = useCallback(
    async (isDepartment: boolean) => {
      const deptIds = Array.from(batchSelected)
      if (!deptIds.length) return

      // Sum the per-node previews. Each may be `unavailable` for a large tenant;
      // if ANY is, the total is not a number we can stand behind.
      let departments = 0
      let libraries = 0
      let unavailable = false
      for (const id of deptIds) {
        try {
          const impact = (await getNodeTypeImpactApi(id)) as NodeTypeImpact
          if (impact?.unavailable) {
            unavailable = true
            break
          }
          departments += impact?.affected_departments ?? 0
          libraries += impact?.affected_libraries ?? 0
        } catch {
          unavailable = true
          break
        }
      }

      const desc = unavailable
        ? t("bs:department.impactUnavailable")
        : departments === 0 && libraries === 0
          ? t("bs:department.impactZero")
          : t("bs:department.impactBody", { departments, libraries })

      const confirmed = await new Promise<boolean>((resolve) => {
        let settled = false
        const settle = (v: boolean) => {
          if (settled) return
          settled = true
          resolve(v)
        }
        bsConfirm({
          title: t("bs:department.impactTitle"),
          desc,
          okTxt: t("bs:department.impactConfirm"),
          onOk: () => settle(true),
          onCancel: () => settle(false),
          onClose: () => settle(false),
        })
      })
      if (!confirmed) return

      setBatchSaving(true)
      try {
        const res = await captureAndAlertRequestErrorHoc(batchSetNodeTypeApi(deptIds, isDepartment))
        if (res === null || res === false) return
        setBatchSelected(new Set())
        setBatchMode(false)
        await tree.refreshAll()
      } finally {
        setBatchSaving(false)
      }
    },
    [batchSelected, t, tree],
  )

  // Nav-tree row extras: child-tenant mount badge + quick "create child" button.
  const renderRowSuffix = useCallback(
    (node: DepartmentTreeNode) => (
      <>
        {node.is_tenant_root && (
          <span
            className="mr-1 rounded bg-primary/10 px-1 py-0.5 text-[10px] font-medium text-primary"
            title={t("bs:tenant.mountedBadge", { defaultValue: "子租户挂载点" })}
          >
            {t("bs:tenant.mountedTag", { defaultValue: "子租户" })}
          </span>
        )}
        {node.status !== "archived" && (
          <button
            className="hidden h-5 w-5 shrink-0 items-center justify-center rounded hover:bg-gray-200 group-hover:flex"
            onClick={(e) => {
              e.stopPropagation()
              handleCreateClick(node.id)
            }}
            title={t("bs:department.create")}
          >
            <Plus className="h-3.5 w-3.5" />
          </button>
        )}
      </>
    ),
    [handleCreateClick, t]
  )

  return (
    <div className="flex h-full w-full">
      {/* Left tree panel */}
      <div className="flex w-[280px] min-w-[240px] flex-col border-r bg-background p-4">
        <h2 className="mb-4 text-lg font-semibold">{t("bs:department.tree")}</h2>
        {/* F062: the feature ships inert (every node defaults to 机构), so the
            operator has to be told that annotating is what turns it on. */}
        {/* F062 (AC-08): the bulk entry. Sits next to the tree rather than in a
            menu because annotating a whole org tree is the FIRST thing an
            operator does with this feature, and doing it one node at a time is
            the difference between a task and a chore. Hidden entirely for roles
            the backend would refuse. */}
        {canAnnotate && !batchMode && (
          <button
            className="mb-2 w-full shrink-0 rounded-md border border-dashed border-border-base py-1.5 text-xs text-text-3 hover:border-primary hover:text-primary"
            onClick={() => setBatchMode(true)}
          >
            {t("bs:department.batchMark")}
          </button>
        )}
        <NodeTypeGuideBanner
          hasAnyDepartment={hasAnyDepartmentLoaded}
          onAnnotate={() => setActiveTab("settings")}
        />
        {batchMode && (
          <div className="mb-2 shrink-0 rounded-md border border-border-base bg-fill-1 p-2">
            <div className="mb-2 text-xs text-text-3">
              {t("bs:department.batchSelected", { count: batchSelected.size })}
            </div>
            <div className="flex flex-wrap items-center gap-1.5">
              <Button
                size="sm"
                disabled={batchSelected.size === 0 || batchSaving}
                onClick={() => void handleBatchApply(true)}
              >
                {batchSaving ? t("bs:department.batchSaving") : t("bs:department.nodeTypeDept")}
              </Button>
              <Button
                size="sm"
                variant="outline"
                disabled={batchSelected.size === 0 || batchSaving}
                onClick={() => void handleBatchApply(false)}
              >
                {t("bs:department.nodeTypeOrg")}
              </Button>
              <Button
                size="sm"
                variant="ghost"
                disabled={batchSaving}
                onClick={() => {
                  setBatchSelected(new Set())
                  setBatchMode(false)
                }}
              >
                {t("cancel")}
              </Button>
            </div>
          </div>
        )}
        <LazyDepartmentTree
          controller={tree}
          selectedDeptId={selectedDeptId}
          onSelect={selectNode}
          renderRowPrefix={batchMode ? renderRowCheckbox : undefined}
          renderRowSuffix={renderRowSuffix}
          rowRef={(deptId, el) => {
            if (el) rowRefs.current.set(deptId, el)
            else rowRefs.current.delete(deptId)
          }}
        />
        <button
          className="mt-4 w-full rounded-md border border-dashed border-gray-300 py-2 text-sm text-gray-500 hover:border-primary hover:text-primary"
          onClick={() => handleCreateClick()}
        >
          + {t("bs:department.create")}
        </button>
      </div>

      {/* Right panel */}
      <div className="flex-1 overflow-auto p-4">
        {selectedDept ? (
          <Tabs value={activeTab} onValueChange={setActiveTab} className="w-full">
            <div className="mb-4 flex items-center justify-between">
              <div>
                <h2 className="text-lg font-semibold">
                  {selectedDept.name}
                  {selectedDept.status === "archived" ? ` ${t("bs:department.archivedTag")}` : ""}
                </h2>
              </div>
              <TabsList>
                <TabsTrigger value="members">{t("bs:department.members")}</TabsTrigger>
                <TabsTrigger value="settings">{t("bs:department.settings")}</TabsTrigger>
              </TabsList>
            </div>
            <TabsContent value="members">
              <MemberTable
                deptId={selectedDept.dept_id}
                deptName={selectedDept.name}
                dept={selectedDept}
                isArchived={selectedDept.status === "archived"}
                onChanged={handleTreeChange}
                membersRefreshSignal={membersRefreshSignal}
                highlightUserId={memberHighlightUserId}
                onHighlightConsumed={handleMemberHighlightConsumed}
                onRequestLocateMember={handleLocateMemberFromGlobal}
              />
            </TabsContent>
            <TabsContent value="settings">
              <DepartmentSettings
                dept={selectedDept}
                onChanged={handleDepartmentSettingsChanged}
                onMarkAsTenant={canMountTenant ? handleMarkAsTenant : undefined}
              />
            </TabsContent>
          </Tabs>
        ) : (
          <div className="flex h-full items-center justify-center text-muted-foreground">
            {t("bs:department.selectDept")}
          </div>
        )}
      </div>

      {/* Create dialog */}
      {createOpen && (
        <CreateDepartmentDialog
          defaultParentId={createParentId}
          onCreated={handleCreated}
          onClose={() => setCreateOpen(false)}
        />
      )}

      {/* Mount as Child Tenant dialog */}
      {mountTarget && (
        <MountTenantDialog
          deptId={mountTarget.id}
          deptName={mountTarget.name}
          onMounted={handleMounted}
          onClose={() => setMountTarget(null)}
        />
      )}
    </div>
  )
}
