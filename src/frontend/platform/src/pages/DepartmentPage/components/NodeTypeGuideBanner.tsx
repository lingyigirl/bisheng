/**
 * F062 (AC-08): one-time hint shown while the org tree is unannotated.
 *
 * WHY THIS EXISTS AT ALL
 * ----------------------
 * The migration deliberately defaults every node to 机构, which makes the new
 * visibility rule collapse onto the previous behaviour — that default is what
 * makes the upgrade safe (see the migration's docstring). The cost is that the
 * feature ships INERT: nothing changes until someone marks nodes as 部门. A
 * customer who never learns that will report "the feature does not work".
 *
 * So this banner is not onboarding polish; it is the discoverability half of a
 * deliberate safety decision.
 *
 * WHY IT DISMISSES BY ITSELF
 * --------------------------
 * The condition is "no node is a 部门 yet", evaluated from live data — so the
 * banner disappears on its own the moment the tree is annotated, with no
 * acknowledgement to track. The dismiss button exists for the operator who has
 * decided not to annotate (or not yet); it is remembered per user, and the
 * banner returns for anyone who never dismissed it.
 */
import { Button } from "@/components/bs-ui/button"
import { userContext } from "@/contexts/userContext"
import { Info } from "lucide-react"
import { useContext, useEffect, useState } from "react"
import { useTranslation } from "react-i18next"

/** Per-user, not global: the decision to hide a hint is personal. */
const DISMISS_KEY_PREFIX = "bs:dept-node-type-guide-dismissed:"

interface NodeTypeGuideBannerProps {
  /** True when the tenant has at least one node already marked as 部门. */
  hasAnyDepartment: boolean
  /** Jump to the settings tab of the selected node so annotating is one click away. */
  onAnnotate: () => void
}

export function NodeTypeGuideBanner({ hasAnyDepartment, onAnnotate }: NodeTypeGuideBannerProps) {
  const { t } = useTranslation()
  const { user } = useContext(userContext) as { user: { user_id?: number } | null }
  const dismissKey = `${DISMISS_KEY_PREFIX}${user?.user_id ?? "anon"}`
  const [dismissed, setDismissed] = useState(true)

  // Read the flag in an effect, not in useState's initializer: localStorage is
  // not available during SSR/initial render and reading it there would make the
  // first paint differ from the server's.
  useEffect(() => {
    try {
      setDismissed(localStorage.getItem(dismissKey) === "1")
    } catch {
      setDismissed(false)
    }
  }, [dismissKey])

  if (hasAnyDepartment || dismissed) return null

  const handleDismiss = () => {
    try {
      localStorage.setItem(dismissKey, "1")
    } catch {
      // Storage unavailable (private mode / quota) — the banner simply returns
      // next time. Not worth surfacing an error for.
    }
    setDismissed(true)
  }

  return (
    <div className="mb-3 flex items-start gap-2 rounded-md border border-border-base bg-fill-1 p-3 text-sm">
      <Info className="mt-0.5 h-4 w-4 shrink-0 text-text-3" />
      <div className="min-w-0 flex-1">
        <div className="font-medium text-text-1">{t("bs:department.guideTitle")}</div>
        <p className="mt-1 text-text-3">{t("bs:department.guideBody")}</p>
        <div className="mt-2 flex items-center gap-2">
          <Button size="sm" onClick={onAnnotate}>
            {t("bs:department.guideAction")}
          </Button>
          <Button size="sm" variant="ghost" onClick={handleDismiss}>
            {t("bs:department.guideDismiss")}
          </Button>
        </div>
      </div>
    </div>
  )
}
