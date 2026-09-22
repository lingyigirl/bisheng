import { Button } from "@/components/bs-ui/button"
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/bs-ui/dialog"
import { Input } from "@/components/bs-ui/input"
import { Label } from "@/components/bs-ui/label"
import { RadioGroup, RadioGroupItem } from "@/components/bs-ui/radio-group"
import { toast } from "@/components/bs-ui/toast/use-toast"
import { TreeDepartmentSelect } from "@/components/bs-comp/department"
import DepartmentUsersSelect, {
  DepartmentUserOption,
} from "@/components/bs-comp/selectComponent/DepartmentUsersSelect"
import { createDepartmentApi } from "@/controllers/API/department"
import { captureAndAlertRequestErrorHoc } from "@/controllers/request"
import { useCallback, useState } from "react"
import { useTranslation } from "react-i18next"

interface CreateDepartmentDialogProps {
  defaultParentId: number | null
  onCreated: () => void
  onClose: () => void
}

export function CreateDepartmentDialog({
  defaultParentId,
  onCreated,
  onClose,
}: CreateDepartmentDialogProps) {
  const { t } = useTranslation()
  const [name, setName] = useState("")
  const [parentId, setParentId] = useState<number | null>(defaultParentId)
  /* F062 node marker. A literal initial value, deliberately NOT derived from the
     selected parent: AC-03 forbids inheriting the parent's marker, because a new
     node under a 部门 parent must still default to 机构 (the isolating state). */
  const [nodeType, setNodeType] = useState<"org" | "dept">("org")
  const [adminSelectValue, setAdminSelectValue] = useState<DepartmentUserOption[]>([])
  const [loading, setLoading] = useState(false)

  const handleSubmit = useCallback(() => {
    if (!name || name.length < 2 || name.length > 50) {
      toast({
        title: t("prompt"),
        description: t("bs:department.nameLength"),
        variant: "error",
      })
      return
    }
    if (parentId === null) {
      toast({
        title: t("prompt"),
        description: t("bs:department.selectParent"),
        variant: "error",
      })
      return
    }
    setLoading(true)
    captureAndAlertRequestErrorHoc(
      createDepartmentApi({
        name,
        parent_id: parentId,
        is_department: nodeType === "dept",
        admin_user_ids: adminSelectValue.length
          ? adminSelectValue.map((o) => o.value)
          : undefined,
      })
    ).then((res) => {
      setLoading(false)
      // captureAndAlertRequestErrorHoc 在接口失败时返回 false（不是 null）
      if (res === false) return
      toast({
        title: t("prompt"),
        description: t("bs:department.create"),
        variant: "success",
      })
      onCreated()
    })
  }, [name, parentId, nodeType, adminSelectValue, onCreated, t])

  return (
    <Dialog open onOpenChange={(open) => !open && onClose()}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>{t("bs:department.create")}</DialogTitle>
        </DialogHeader>
        <div className="space-y-4 py-4">
          {/* Parent department */}
          <div className="space-y-2">
            <Label>{t("bs:department.parentDept")} *</Label>
            <TreeDepartmentSelect
              value={parentId}
              onChange={(id) => setParentId(id)}
              modal={false}
              placeholder={t("bs:department.selectParent")}
              searchPlaceholder={t("bs:department.search")}
              className="max-w-xl"
            />
          </div>

          {/* Department name */}
          <div className="space-y-2">
            <Label>{t("bs:department.name")} *</Label>
            <Input
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder={t("bs:department.nameRequired")}
              maxLength={50}
            />
          </div>

          {/* F062: same control as the 部门设置 tab — same keys, same layout,
              deliberately NO `disabled` (see the `is_department` note on
              `DepartmentCreateForm`).
              The ids differ from the settings tab's `f062-node-type-*` because
              both can be mounted at once: the settings TabsContent stays mounted
              while it is the ACTIVE tab (Radix unmounts inactive content), and
              neither the tree row's "+" nor the bottom "+" switches tabs. This
              dialog portals to the end of <body>, so a shared id would resolve
              to the settings radios — leaving these two radios unlabelled, since
              their accessible names come only from the labels below. */}
          <div className="space-y-1.5">
            <Label>{t("bs:department.nodeTypeLabel")}</Label>
            <RadioGroup
              value={nodeType}
              onValueChange={(v) => setNodeType(v as "org" | "dept")}
              className="gap-2"
            >
              <div className="flex items-start gap-2">
                <RadioGroupItem value="org" id="create-dept-node-type-org" className="mt-0.5" />
                <div className="space-y-0.5">
                  <Label htmlFor="create-dept-node-type-org" className="font-normal">
                    {t("bs:department.nodeTypeOrg")}
                  </Label>
                  <p className="text-xs text-text-3">{t("bs:department.nodeTypeOrgHint")}</p>
                </div>
              </div>
              <div className="flex items-start gap-2">
                <RadioGroupItem value="dept" id="create-dept-node-type-dept" className="mt-0.5" />
                <div className="space-y-0.5">
                  <Label htmlFor="create-dept-node-type-dept" className="font-normal">
                    {t("bs:department.nodeTypeDept")}
                  </Label>
                  <p className="text-xs text-text-3">{t("bs:department.nodeTypeDeptHint")}</p>
                </div>
              </div>
            </RadioGroup>
          </div>

          {/* Department admins (optional) — 与部门设置页相同的多选搜索 */}
          <div className="space-y-2">
            <Label>{t("bs:department.admins")}</Label>
            <DepartmentUsersSelect
              multiple
              value={adminSelectValue}
              onChange={(vals) => {
                const v = (vals as DepartmentUserOption[]) || []
                setAdminSelectValue(v)
              }}
              placeholder={t("bs:department.adminSelectPlaceholder")}
              searchPlaceholder={t("bs:department.searchUsersPlaceholder")}
              className="max-w-xl w-full"
            />
          </div>
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={onClose}>
            {t("cancel")}
          </Button>
          <Button onClick={handleSubmit} disabled={loading}>
            {t("confirmButton")}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
